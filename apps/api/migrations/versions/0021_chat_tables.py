"""0021 — Chat IA: tablas chat_sessions y chat_messages (Fase 0 del plan Chat IA + MCP)."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0021_chat_tables"
down_revision = "0020_dedup_by_filename"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "026_chat_tables.sql"
    content = sql.read_text(encoding="utf-8").replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_messages, chat_sessions CASCADE")
