"""0043 — Catálogo canónico de subtipos para eventos procesales.

- Agrega ``events.subtype_raw`` para conservar el valor original del LLM.
- Restringe ``events.subtype`` al catálogo canónico definido en
  ``app.domain.procedural_catalog``.
- Normaliza los subtipos existentes y guarda el texto original en
  ``subtype_raw`` cuando difiera del canónico.
"""
from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0043_subtype_catalog"
down_revision = "0042_event_relationships_to_graph"
branch_labels = None
depends_on = None

# Permite importar app.domain.procedural_catalog cuando alembic corre fuera del
# contenedor (el directorio padre de migrations/ es apps/api).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def upgrade() -> None:
    op.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS subtype_raw text")

    from app.domain.procedural_catalog import normalize_subtype, PROCEDURAL_SUBTYPES

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, subtype FROM events WHERE subtype IS NOT NULL"))
    for row in rows.mappings():
        raw = row["subtype"]
        canonical = normalize_subtype(raw)
        if canonical == raw:
            continue
        conn.execute(
            sa.text("UPDATE events SET subtype = :c, subtype_raw = :r WHERE id = :id"),
            {"c": canonical, "r": raw if canonical is None else raw, "id": row["id"]},
        )

    allowed = ", ".join(f"'{s}'" for s in sorted(PROCEDURAL_SUBTYPES))
    op.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS events_subtype_check")
    op.execute(
        f"ALTER TABLE events ADD CONSTRAINT events_subtype_check "
        f"CHECK (subtype IS NULL OR subtype IN ({allowed}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS events_subtype_check")
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS subtype_raw")
