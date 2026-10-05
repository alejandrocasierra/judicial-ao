-- Asegura RLS tenant en las tablas de administración (idempotente).
-- La migración 0008 se aplicó antes de añadir el bloque RLS al script 011, por lo
-- que en algunas bases las tablas quedaron sin aislamiento por organización.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['smtp_settings','agents','skills','ai_models','backups'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = t AND policyname = 'tenant_isolation') THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) WITH CHECK (organization_id = current_org())', t);
    END IF;
  END LOOP;
END $$;
