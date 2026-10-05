"""0013 — módulo Procesos: carpetas por expediente + archivos genéricos."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0013_process_folders"
down_revision = "0012_admin_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "016_process_folders.sql"
    content = sql.read_text(encoding="utf-8").replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS folder_id")
    op.execute("ALTER TABLE media DROP COLUMN IF EXISTS folder_id")
    op.execute("DROP TABLE IF EXISTS case_files, case_folders CASCADE")
