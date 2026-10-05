"""Cadena de auditoría: el id se asigna DENTRO del candado de la cadena.

Bug expuesto por escrituras concurrentes (test_it_wrk_06): PostgreSQL evalúa el
DEFAULT (nextval) ANTES de disparar el BEFORE trigger, así que dos inserciones
concurrentes obtenían sus ids antes de competir por el candado de la cadena y el
orden por id dejaba de coincidir con el orden de encadenamiento (T8, SSD §20.4).
Ahora el trigger toma el nextval tras adquirir el candado: id order == chain order.
"""
from alembic import op

revision = "0003_audit_chain_id_order"
down_revision = "0002_jobs_stale_sweeper"
branch_labels = None
depends_on = None


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
CREATE OR REPLACE FUNCTION audit_chain() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE last_hash char(64);
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('audit_chain'));
  NEW.id := nextval('audit_logs_id_seq');
  SELECT hash INTO last_hash FROM audit_logs ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := last_hash;
  NEW.hash := encode(digest(coalesce(last_hash,'') || NEW.id::text || coalesce(NEW.organization_id::text,'') ||
              coalesce(NEW.actor_id::text,'') || NEW.action || coalesce(NEW.entity_id,'') ||
              coalesce(NEW.before::text,'') || coalesce(NEW.after::text,'') || NEW.created_at::text, 'sha256'), 'hex');
  RETURN NEW;
END $$;

-- El id lo fija el trigger dentro del candado; sin default no hay nextval anticipado.
ALTER TABLE audit_logs ALTER COLUMN id DROP DEFAULT;
"""

DOWN = """
ALTER TABLE audit_logs ALTER COLUMN id SET DEFAULT nextval('audit_logs_id_seq');

CREATE OR REPLACE FUNCTION audit_chain() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE last_hash char(64);
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('audit_chain'));
  SELECT hash INTO last_hash FROM audit_logs ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := last_hash;
  NEW.hash := encode(digest(coalesce(last_hash,'') || NEW.id::text || coalesce(NEW.organization_id::text,'') ||
              coalesce(NEW.actor_id::text,'') || NEW.action || coalesce(NEW.entity_id,'') ||
              coalesce(NEW.before::text,'') || coalesce(NEW.after::text,'') || NEW.created_at::text, 'sha256'), 'hex');
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    _raw(UP)


def downgrade() -> None:
    _raw(DOWN)
