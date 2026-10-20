"""0047 — Pipeline de chat: answer_kind, memoria resumida y skills ejecutables."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0047_chat_pipeline"
down_revision = "0046_chat_ocr_asr_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "047_chat_pipeline.sql"
    content = sql.read_text(encoding="utf-8")
    op.execute(content)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS answer_kind")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS intent")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS degraded")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS summary")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS summary_upto")
    op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS tools")
    op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS parameters")
    op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS position")
