"""0030 — eventos PROCESALES: columnas de instancia, actor, autoridad, tipo de fecha y efecto.

Separa los eventos genéricos (`kind='generic'`) de las actuaciones procesales
(`kind='procedural'`) para poder construir una línea de tiempo procesal real.
"""
from alembic import op

revision = "0030_procedural_events"
down_revision = "0029_speaker_role_freetext"
branch_labels = None
depends_on = None

_COLS = [
    "ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'generic'",
    "ADD COLUMN IF NOT EXISTS subtype text",
    "ADD COLUMN IF NOT EXISTS instance text",
    "ADD COLUMN IF NOT EXISTS actor text",
    "ADD COLUMN IF NOT EXISTS authority text",
    "ADD COLUMN IF NOT EXISTS date_type text",
    "ADD COLUMN IF NOT EXISTS procedural_effect text",
    "ADD COLUMN IF NOT EXISTS document_id uuid REFERENCES documents(id)",
    "ADD COLUMN IF NOT EXISTS page_number int",
]


def upgrade() -> None:
    for c in _COLS:
        op.execute(f"ALTER TABLE events {c}")
    op.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS events_kind_check")
    op.execute("ALTER TABLE events ADD CONSTRAINT events_kind_check CHECK (kind IN ('generic','procedural'))")


def downgrade() -> None:
    op.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS events_kind_check")
    for col in ("page_number", "document_id", "procedural_effect", "date_type", "authority",
                "actor", "instance", "subtype", "kind"):
        op.execute(f"ALTER TABLE events DROP COLUMN IF EXISTS {col}")
