"""0046 — Chat IA: preferencia de motor OCR/ASR por sesión."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0046_chat_ocr_asr_mode"
down_revision = "0045_party_role_sides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "046_chat_ocr_asr_mode.sql"
    content = sql.read_text(encoding="utf-8").replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS ocr_mode")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS asr_mode")
