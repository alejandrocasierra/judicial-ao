"""0025 — URL base por modelo (override del endpoint del proveedor)."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0025_model_base_url"
down_revision = "0024_pending_corrections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "030_model_base_url.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE ai_models DROP COLUMN IF EXISTS api_base_url")
