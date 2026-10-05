"""0022 — archivado de sesiones de chat (soft-delete)."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0022_chat_archive"
down_revision = "0021_chat_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "027_chat_archive.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS archived_at")
