"""0018 — progreso del OCR en vivo (tabla separada para evitar el lock de documents)."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0018_ocr_progress"
down_revision = "0017_ocr_mode_and_versions"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "023_ocr_progress.sql"


def upgrade() -> None:
    content = _SQL.read_text(encoding="utf-8").replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_ocr_progress CASCADE")
