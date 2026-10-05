"""0024 — correcciones pendientes de confirmación (Fase 6)."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0024_pending_corrections"
down_revision = "0023_skill_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "029_pending_corrections.sql"
    content = sql.read_text(encoding="utf-8").replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_pending_corrections CASCADE")
