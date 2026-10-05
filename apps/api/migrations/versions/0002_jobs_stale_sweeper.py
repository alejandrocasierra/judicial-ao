"""Jobs: created_by (actor que originó el job) + función jobs_reap_stale.

El sweeper de jobs huérfanos (RUNNING/RETRYING sin progreso) debe barrer TODAS
las organizaciones, pero el rol de la aplicación vive bajo RLS FORCE: sin
app.current_org no ve ninguna fila. La función es SECURITY DEFINER (la crea el
rol owner, que tiene BYPASSRLS) con search_path fijado, siguiendo el patrón de
las funciones de auth de 040_auth_functions.sql. Sólo toca metadatos de jobs,
nunca contenido del expediente.

created_by permite auditar cada recuperación con el actor que originó el job
(test_sec_aud_04 exige actor/org no nulos fuera de acciones auth.*).

{{VAR}} se reemplaza con variables de entorno (misma lista blanca que 0001).
"""
import os
import re

from alembic import op

revision = "0002_jobs_stale_sweeper"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

ALLOWED_VARS = {"DB_APP_USER": r"^[a-z_][a-z0-9_]{0,62}$"}


def render(sql: str) -> str:
    def sub(m):
        name = m.group(1)
        if name not in ALLOWED_VARS:
            raise RuntimeError(f"template variable not allowed: {name}")
        val = os.environ.get(name, "")
        if not re.fullmatch(ALLOWED_VARS[name], val):
            raise RuntimeError(f"invalid value for {name}")
        return val
    return re.sub(r"\{\{([A-Z_]+)\}\}", sub, sql)


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
ALTER TABLE jobs ADD COLUMN created_by uuid REFERENCES users(id);

-- Devuelve a QUEUED los jobs huérfanos (status RUNNING/RETRYING con updated_at
-- más viejo que p_stale_minutes) y devuelve sus metadatos para auditoría.
-- SKIP LOCKED: dos sweeps concurrentes no compiten por la misma fila.
CREATE FUNCTION jobs_reap_stale(p_stale_minutes int)
RETURNS TABLE (id uuid, organization_id uuid, case_id uuid, job_type text, previous_status text, created_by uuid)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  WITH stale AS (
    SELECT j.id, j.organization_id, j.case_id, j.job_type, j.status, j.created_by
    FROM jobs j
    WHERE j.status IN ('RUNNING', 'RETRYING')
      AND j.updated_at < now() - make_interval(mins => p_stale_minutes)
    FOR UPDATE SKIP LOCKED
  ), requeued AS (
    UPDATE jobs j SET status = 'QUEUED', error_code = 'stale_requeued'
    FROM stale s WHERE j.id = s.id
    RETURNING j.id
  )
  SELECT s.id, s.organization_id, s.case_id, s.job_type, s.status, s.created_by
  FROM stale s JOIN requeued r ON r.id = s.id
$$;

REVOKE ALL ON FUNCTION jobs_reap_stale(int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION jobs_reap_stale(int) TO "{{DB_APP_USER}}";
"""

DOWN = """
REVOKE ALL ON FUNCTION jobs_reap_stale(int) FROM "{{DB_APP_USER}}";
DROP FUNCTION IF EXISTS jobs_reap_stale(int);
ALTER TABLE jobs DROP COLUMN IF EXISTS created_by;
"""


def upgrade() -> None:
    _raw(render(UP))


def downgrade() -> None:
    _raw(render(DOWN))
