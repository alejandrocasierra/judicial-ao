"""0016 — corrección humana del OCR de una página (no la pisa un reproceso)."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0016_ocr_human_correction"
down_revision = "0015_model_task_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "019_ocr_human_correction.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_pages_human_corrected")
    op.execute("ALTER TABLE document_pages DROP COLUMN IF EXISTS corrected_by, "
               "DROP COLUMN IF EXISTS corrected_at, DROP COLUMN IF EXISTS human_corrected")
