"""0041 — fechas separadas en events: document_date e incorporation_date.

Permite distinguir la fecha que narra el evento (event_date), la fecha propia del
documento que lo contiene (document_date) y la fecha en que ese documento/medio
fue incorporado al expediente (incorporation_date).
"""
from __future__ import annotations

from alembic import op

revision = "0041_event_dates"
down_revision = "0040_transcript_words"
branch_labels = None
depends_on = None


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
ALTER TABLE events
    ADD COLUMN IF NOT EXISTS document_date date,
    ADD COLUMN IF NOT EXISTS incorporation_date date;

-- Eventos vinculados a un documento: document_date = fecha del documento,
-- fallback a la fecha del evento; incorporation_date = fecha de carga.
UPDATE events e
SET document_date = COALESCE(d.document_date, e.event_date),
    incorporation_date = d.created_at::date
FROM documents d
WHERE e.document_id = d.id;

-- Eventos sin documento pero con cita a medio: incorporation_date = fecha de carga del medio.
UPDATE events e
SET incorporation_date = sub.first_media_date
FROM (
    SELECT c.target_id AS event_id, MIN(m.created_at::date) AS first_media_date
    FROM citations c
    JOIN media m ON m.id = c.media_id
    WHERE c.target_type = 'event'
    GROUP BY c.target_id
) sub
WHERE e.id = sub.event_id
  AND e.document_id IS NULL
  AND e.incorporation_date IS NULL;
"""

DOWN = """
ALTER TABLE events
    DROP COLUMN IF EXISTS document_date,
    DROP COLUMN IF EXISTS incorporation_date;
"""


def upgrade() -> None:
    _raw(UP)


def downgrade() -> None:
    _raw(DOWN)
