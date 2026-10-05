"""0023 — marca de skills de sistema (badge "Sistema" en el panel)."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0023_skill_system"
down_revision = "0022_chat_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "028_skill_system.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS is_system")
