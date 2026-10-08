"""jobs_reap_stale: no barrer los jobs de medios (ASR/diarización).

Los jobs de medios corren en la tarea `jobs.run_media` con su PROPIO `time_limit`
largos (pueden tardar horas con pyannote en audios de horas). El sweeper de huérfanos
usa un umbral corto pensado para jobs de segundos, así que marcaría como "stale" un
job de medios en plena ejecución y lo reencolaría (duplicando trabajo pesado). Se
excluyen del barrido; siguen acotados por el `time_limit` de su propia tarea.

{{VAR}} se reemplaza con variables de entorno (misma lista blanca que 0002).
"""
import os
import re

from alembic import op

revision = "0037_jobs_reap_skip_media"
down_revision = "0036_media_asr_progress"
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
CREATE OR REPLACE FUNCTION jobs_reap_stale(p_stale_minutes int)
RETURNS TABLE (id uuid, organization_id uuid, case_id uuid, job_type text, previous_status text, created_by uuid)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  WITH stale AS (
    SELECT j.id, j.organization_id, j.case_id, j.job_type, j.status, j.created_by
    FROM jobs j
    WHERE j.status IN ('RUNNING', 'RETRYING')
      -- Los medios (ASR/diarización) tienen su propio time_limit largo; no se barren.
      AND j.job_type NOT IN ('media_asr', 'media_diarize')
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
CREATE OR REPLACE FUNCTION jobs_reap_stale(p_stale_minutes int)
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


def upgrade() -> None:
    _raw(render(UP))


def downgrade() -> None:
    _raw(render(DOWN))
