"""0050 — RLS forzado en party_roles."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0050_party_roles_rls"
down_revision = "0049_fts_spanish_unaccent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "050_party_roles_rls.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON party_roles")
    op.execute("ALTER TABLE party_roles NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE party_roles DISABLE ROW LEVEL SECURITY")
