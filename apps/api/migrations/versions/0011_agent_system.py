"""0011 — agentes de sistema (integrados en el runtime)."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0011_agent_system"
down_revision = "0010_admin_crud"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "014_agent_system.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS is_system, DROP COLUMN IF EXISTS kind")
