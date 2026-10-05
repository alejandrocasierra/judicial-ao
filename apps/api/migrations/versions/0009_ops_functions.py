"""0009 — funciones operativas SECURITY DEFINER (backups programados)."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0009_ops_functions"
down_revision = "0008_admin_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "012_ops_functions.sql"
    content = sql.read_text(encoding="utf-8")
    content = content.replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS ops_list_organizations()")
