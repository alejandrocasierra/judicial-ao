"""0048 — Historial de chat completo: incertidumbres y metadatos de ocurrencias."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0048_chat_history_metadata"
down_revision = "0047_chat_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "048_chat_history_metadata.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS uncertainties")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS occurrences_term")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS occurrences_note")
