-- Chat IA: conversaciones multi-turn por expediente (Fase 0 del plan Chat IA + MCP).
--
-- chat_sessions: una conversación pertenece a un expediente y recuerda el agente
--                (elegido con "/") y el modelo (selector del widget) con que se inició.
-- chat_messages: mensajes del hilo. `attachments` guarda los "@" REALES
--                [{kind:'document'|'media', id, name}] y `citations` las citas de la
--                respuesta; model_run_id enlaza con la trazabilidad del pipeline de IA.

CREATE TABLE IF NOT EXISTS chat_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  title text NOT NULL DEFAULT '',
  agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
  model_id uuid REFERENCES ai_models(id) ON DELETE SET NULL,
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chat_sessions_case ON chat_sessions(case_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_chat_sessions_org ON chat_sessions(organization_id);

CREATE TABLE IF NOT EXISTS chat_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  session_id uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('user','assistant','tool')),
  content text NOT NULL DEFAULT '',
  attachments jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(attachments) = 'array'),
  citations jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(citations) = 'array'),
  model_run_id uuid REFERENCES model_runs(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chat_messages_session ON chat_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_chat_messages_org ON chat_messages(organization_id);

-- RLS tenant (mismo patrón que el resto de tablas).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['chat_sessions','chat_messages'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = t AND policyname = 'tenant_isolation') THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) WITH CHECK (organization_id = current_org())', t);
    END IF;
  END LOOP;
END $$;

-- Integridad multi-tenant: un hijo no puede apuntar a un padre de otra organización.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
    ('chat_sessions','cases','case_id'),
    ('chat_sessions','agents','agent_id'),
    ('chat_sessions','ai_models','model_id'),
    ('chat_sessions','users','created_by'),
    ('chat_messages','chat_sessions','session_id'),
    ('chat_messages','model_runs','model_run_id')
  ) AS t(child, parent, col) LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = format('trg_same_org_%s_%s', r.child, r.col)) THEN
      EXECUTE format('CREATE TRIGGER trg_same_org_%s_%s BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION enforce_same_org(%L, %L)',
                     r.child, r.col, r.child, r.parent, r.col);
    END IF;
  END LOOP;
END $$;

-- updated_at automático en las sesiones.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_chat_sessions_touch') THEN
    CREATE TRIGGER trg_chat_sessions_touch BEFORE UPDATE ON chat_sessions FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON chat_sessions, chat_messages TO "{{DB_APP_USER}}";
