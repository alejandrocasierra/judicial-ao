"""0015 — flags ocr_enabled/asr_enabled en ai_models (uno activo por tipo y org)."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0015_model_task_flags"
down_revision = "0014_case_purge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "018_model_task_flags.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_ai_models_asr_enabled")
    op.execute("DROP INDEX IF EXISTS ux_ai_models_ocr_enabled")
    op.execute("ALTER TABLE ai_models DROP COLUMN IF EXISTS ocr_enabled, DROP COLUMN IF EXISTS asr_enabled")
