-- =====================================================================
-- Row Level Security (SSD §25.3). FORCE: aplica a todo rol sin BYPASSRLS.
-- El dueño (migraciones + funciones SECURITY DEFINER acotadas) tiene BYPASSRLS;
-- la API usa DB_APP_USER, que NO lo tiene.
-- Sin app.current_org => cero filas.
-- =====================================================================
CREATE FUNCTION current_org() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.current_org', true), '')::uuid
$$;

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON organizations USING (id = current_org()) WITH CHECK (id = current_org());

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users','refresh_tokens','cases','case_members','parties','documents','document_pages','media',
    'speakers','transcript_segments','entities','decisions','events','claims','facts','fact_claims','evidence','evidence_links',
    'citations','contradictions','legal_rules','issues','jobs','model_runs','reviews','chunks','graph_nodes','graph_edges'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) WITH CHECK (organization_id = current_org())', t);
  END LOOP;
END $$;

ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_read ON audit_logs FOR SELECT USING (organization_id = current_org());
CREATE POLICY audit_insert ON audit_logs FOR INSERT WITH CHECK (organization_id IS NULL OR organization_id = current_org());

-- Privilegios del rol de aplicación (mínimo privilegio)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO "{{DB_APP_USER}}";
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO "{{DB_APP_USER}}";
GRANT DELETE ON case_members, refresh_tokens, chunks, graph_nodes, graph_edges TO "{{DB_APP_USER}}";
REVOKE UPDATE ON audit_logs, reviews FROM "{{DB_APP_USER}}";
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "{{DB_APP_USER}}";
REVOKE ALL ON FUNCTION auth_find_user(text), auth_register_failure(uuid,int,int), auth_register_success(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_find_user(text), auth_register_failure(uuid,int,int), auth_register_success(uuid) TO "{{DB_APP_USER}}";
