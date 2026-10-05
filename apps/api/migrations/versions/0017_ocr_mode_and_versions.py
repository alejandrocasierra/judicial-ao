"""0017 — modo de OCR por documento (basico/document_ai) + versiones por motor."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0017_ocr_mode_and_versions"
down_revision = "0016_ocr_human_correction"
branch_labels = None
depends_on = None

_SQL_DIR = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql"


def _apply(filename: str) -> None:
    content = (_SQL_DIR / filename).read_text(encoding="utf-8")
    content = content.replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def upgrade() -> None:
    _apply("021_ocr_mode.sql")
    _apply("022_ocr_versions.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_ocr_versions CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_documents_ocr_mode")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS ocr_mode")
    op.execute("ALTER TABLE media DROP COLUMN IF EXISTS asr_mode")
