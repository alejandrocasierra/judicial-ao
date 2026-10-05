"""0010 — CRUD de agentes/skills/modelos/roles + diccionario OCR."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0010_admin_crud"
down_revision = "0009_ops_functions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "013_admin_crud.sql"
    content = sql.read_text(encoding="utf-8").replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ocr_terms, roles CASCADE")
    op.execute("ALTER TABLE ai_models DROP COLUMN IF EXISTS encrypted_api_key, DROP COLUMN IF EXISTS updated_at")
