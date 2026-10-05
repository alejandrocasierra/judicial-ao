"""0012 — asegura RLS tenant en tablas de administración."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0012_admin_rls"
down_revision = "0011_agent_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "015_admin_rls.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    # No se revierte: desactivar RLS dejaría las tablas sin aislamiento.
    pass
