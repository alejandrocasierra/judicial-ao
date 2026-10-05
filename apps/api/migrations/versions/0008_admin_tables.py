"""0008 — tablas de administración: SMTP, agentes, skills, modelos IA, backups."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0008_admin_tables"
down_revision = "0007_graph_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "011_admin_tables.sql"
    content = sql.read_text(encoding="utf-8")
    content = content.replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS backups, ai_models, skills, agents, smtp_settings CASCADE")
