-- Fase 6: correcciones pendientes de confirmación por sesión de chat.
-- El agente propone un cambio (vista previa) y queda guardado aquí; el servidor
-- lo aplica de forma DETERMINISTA cuando el usuario confirma, sin depender de que
-- el LLM reconstruya los argumentos en el siguiente turno.
CREATE TABLE IF NOT EXISTS chat_pending_corrections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  session_id uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  tool text NOT NULL,
  arguments jsonb NOT NULL,
  summary text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  resolved_reason text
);

CREATE INDEX IF NOT EXISTS ix_pending_corrections_session
  ON chat_pending_corrections(session_id) WHERE resolved_at IS NULL;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['chat_pending_corrections'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = t AND policyname = 'tenant_isolation') THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) WITH CHECK (organization_id = current_org())', t);
    END IF;
  END LOOP;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_same_org_chat_pending_corrections_session_id') THEN
    CREATE TRIGGER trg_same_org_chat_pending_corrections_session_id
      BEFORE INSERT OR UPDATE ON chat_pending_corrections
      FOR EACH ROW EXECUTE FUNCTION enforce_same_org('chat_sessions', 'session_id');
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON chat_pending_corrections TO "{{DB_APP_USER}}";
