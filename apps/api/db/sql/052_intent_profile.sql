-- 052 — Perfil de intención por usuario: ejemplos y estado del aprendizaje.
--
-- El router decide con reglas; este perfil aprende el dialecto de cada usuario.
-- No es fine-tuning: es una memoria pequeña de ejemplos (pregunta + etiqueta)
-- y un voto kNN ponderado en el turno.
--
-- `intent_exemplar` guarda el ejemplo y su vector (mismo espacio que el índice:
-- `EMBEDDING_DIMENSIONS`). `intent_profile` guarda el estado y las métricas que
-- justifican activarlo; sin métricas no se activa.

CREATE TABLE IF NOT EXISTS intent_exemplar (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label text NOT NULL CHECK (label IN
    ('navegar', 'dato', 'analisis', 'procedimental', 'agregacion', 'lectura', 'correccion')),
  question text NOT NULL,
  -- sha256(text_terms.norm(question)): idempotencia del job y "olvidar este ejemplo".
  question_hash text NOT NULL,
  embedding vector({{EMBEDDING_DIMENSIONS}}),
  embedding_model text,
  -- feedback (verdad) > corpus (verdad etiquetada) > counterfactual (inferencia).
  source text NOT NULL CHECK (source IN ('feedback', 'corpus', 'counterfactual')),
  weight numeric(4,3) NOT NULL DEFAULT 1.0 CHECK (weight > 0 AND weight <= 1),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, question_hash)
);

CREATE INDEX IF NOT EXISTS ix_intent_exemplar_user ON intent_exemplar(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_intent_exemplar_org ON intent_exemplar(organization_id);

CREATE TABLE IF NOT EXISTS intent_profile (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- off: sin datos · shadow: mide pero NO decide · active: decide en los turnos
  -- donde la regla no es fuerte (confidence < 0.9).
  status text NOT NULL DEFAULT 'off' CHECK (status IN ('off', 'shadow', 'active')),
  exemplars integer NOT NULL DEFAULT 0,
  judged integer NOT NULL DEFAULT 0,
  version integer NOT NULL DEFAULT 0,
  rules_accuracy numeric(4,3),
  profile_accuracy numeric(4,3),
  navegar_fp_rules integer NOT NULL DEFAULT 0,
  navegar_fp_profile integer NOT NULL DEFAULT 0,
  reason text NOT NULL DEFAULT '',
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  built_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS ix_intent_profile_status ON intent_profile(organization_id, status);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['intent_exemplar', 'intent_profile'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = t
                    AND policyname = 'tenant_isolation') THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) '
                     'WITH CHECK (organization_id = current_org())', t);
    END IF;
  END LOOP;
END $$;

DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
    ('intent_exemplar','users','user_id'),
    ('intent_profile','users','user_id')
  ) AS t(child, parent, col) LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = format('trg_same_org_%s_%s', r.child, r.col)) THEN
      EXECUTE format('CREATE TRIGGER trg_same_org_%s_%s BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION enforce_same_org(%L, %L)',
                     r.child, r.col, r.child, r.parent, r.col);
    END IF;
  END LOOP;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_intent_profile_touch') THEN
    CREATE TRIGGER trg_intent_profile_touch BEFORE UPDATE ON intent_profile
      FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON intent_exemplar, intent_profile TO "{{DB_APP_USER}}";
