-- Funciones operativas SECURITY DEFINER (backups programados, alertas globales).
-- Propiedad del rol dueño (BYPASSRLS): sólo exponen metadatos, nunca contenido jurídico.

CREATE OR REPLACE FUNCTION ops_list_organizations()
RETURNS TABLE (id uuid, name text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT id, name FROM organizations ORDER BY name
$$;

REVOKE ALL ON FUNCTION ops_list_organizations() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops_list_organizations() TO "{{DB_APP_USER}}";
