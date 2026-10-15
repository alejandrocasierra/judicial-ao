"""0044 — Invariante en BD: una actuación procesal SIEMPRE cita su evidencia.

Evita que vuelvan a la línea de tiempo actuaciones "sin fuente" (huérfanas). Se aplica
como CONSTRAINT TRIGGER diferido: al insertar un evento `kind='procedural'` la cita puede
insertarse después dentro de la MISMA transacción, y la comprobación corre al COMMIT.
"""
from __future__ import annotations

from alembic import op

revision = "0044_events_require_citation"
down_revision = "0043_subtype_catalog"
branch_labels = None
depends_on = None


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
CREATE OR REPLACE FUNCTION events_require_citation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.kind = 'procedural'
     AND NOT EXISTS (SELECT 1 FROM citations c
                     WHERE c.target_type = 'event' AND c.target_id = NEW.id) THEN
    RAISE EXCEPTION 'evento procesal % sin cita de evidencia (invariante de trazabilidad)', NEW.id;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS events_require_citation ON events;
CREATE CONSTRAINT TRIGGER events_require_citation
  AFTER INSERT ON events
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION events_require_citation();
"""

DOWN = """
DROP TRIGGER IF EXISTS events_require_citation ON events;
DROP FUNCTION IF EXISTS events_require_citation();
"""


def upgrade() -> None:
    _raw(UP)


def downgrade() -> None:
    _raw(DOWN)
