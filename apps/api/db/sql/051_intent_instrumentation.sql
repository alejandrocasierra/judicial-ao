-- 051 — Instrumentación del router de intención.
--
-- Sin esto el router no se puede auditar ni mejorar: hoy solo se guarda `intent`.
-- Se pierde POR QUÉ se decidió (regla), con cuánta confianza y qué versión del
-- router lo hizo. Y no existe forma de que el usuario diga si la respuesta era
-- la que pedía, así que no hay etiquetas verdaderas que aprender.
--
-- 1) La decisión completa, en el mensaje del asistente (donde ya vive `intent`).
-- 2) `intent_feedback`: un veredicto por usuario y mensaje, con la intención
--    corregida cuando la hubo. Es la fuente de etiquetas del corpus por usuario.

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS intent_confidence numeric(4,3);
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS intent_reason text;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS intent_source text;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS router_version text;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chat_messages_intent_confidence_range') THEN
    ALTER TABLE chat_messages ADD CONSTRAINT chat_messages_intent_confidence_range
      CHECK (intent_confidence IS NULL OR (intent_confidence >= 0 AND intent_confidence <= 1));
  END IF;
END $$;

COMMENT ON COLUMN chat_messages.intent_confidence IS
  'Confianza 0..1 de la decisión del router (NULL = turno determinista sin router)';
COMMENT ON COLUMN chat_messages.intent_reason IS
  'Familia de regla que decidió: default_lookup, explicit_location_signal, correction_phrase…';
COMMENT ON COLUMN chat_messages.intent_source IS
  'rules | llm | override | deterministic (de dónde salió la decisión)';
COMMENT ON COLUMN chat_messages.router_version IS
  'Versión del router que decidió (intent.ROUTER_VERSION); permite comparar tras un cambio';

CREATE TABLE IF NOT EXISTS intent_feedback (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  session_id uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  message_id uuid NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- accepted: era lo que pedía · rejected: no era lo que pedía, sin decir cuál
  -- wrong_intent: no era lo que pedía y el usuario dice cuál era.
  verdict text NOT NULL CHECK (verdict IN ('accepted', 'rejected', 'wrong_intent')),
  corrected_intent text CHECK (corrected_intent IS NULL OR corrected_intent IN
    ('navegar', 'dato', 'analisis', 'procedimental', 'agregacion', 'lectura', 'correccion')),
  note text NOT NULL DEFAULT '',
  -- Snapshot de lo que se estaba juzgando: si mañana cambia el mensaje o el
  -- router, el veredicto sigue siendo interpretable.
  router_intent text,
  router_version text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT intent_feedback_wrong_needs_intent
    CHECK (verdict <> 'wrong_intent' OR corrected_intent IS NOT NULL),
  CONSTRAINT intent_feedback_corrected_only_when_wrong
    CHECK (corrected_intent IS NULL OR verdict = 'wrong_intent'),
  CONSTRAINT intent_feedback_once_per_user UNIQUE (message_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_intent_feedback_org ON intent_feedback(organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_intent_feedback_verdict ON intent_feedback(organization_id, verdict);

-- RLS tenant (mismo patrón que el resto de tablas).
ALTER TABLE intent_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE intent_feedback FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname = 'public' AND tablename = 'intent_feedback'
                    AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON intent_feedback
      USING (organization_id = current_org()) WITH CHECK (organization_id = current_org());
  END IF;
END $$;

-- Integridad multi-tenant: el veredicto no puede apuntar a un mensaje de otra org.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
    ('intent_feedback','cases','case_id'),
    ('intent_feedback','chat_sessions','session_id'),
    ('intent_feedback','chat_messages','message_id'),
    ('intent_feedback','users','user_id')
  ) AS t(child, parent, col) LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = format('trg_same_org_%s_%s', r.child, r.col)) THEN
      EXECUTE format('CREATE TRIGGER trg_same_org_%s_%s BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION enforce_same_org(%L, %L)',
                     r.child, r.col, r.child, r.parent, r.col);
    END IF;
  END LOOP;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_intent_feedback_touch') THEN
    CREATE TRIGGER trg_intent_feedback_touch BEFORE UPDATE ON intent_feedback
      FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON intent_feedback TO "{{DB_APP_USER}}";
