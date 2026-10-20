-- 050 — RLS forzado en party_roles (deuda de aislamiento multitenant).
ALTER TABLE party_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE party_roles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON party_roles;
CREATE POLICY tenant_isolation ON party_roles
  USING (organization_id = current_org())
  WITH CHECK (organization_id = current_org());
