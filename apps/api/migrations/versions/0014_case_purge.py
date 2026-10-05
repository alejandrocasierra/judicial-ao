"""0014 — purga controlada de expedientes (bypass por bandera + grants DELETE)."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0014_case_purge"
down_revision = "0013_process_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "017_case_purge.sql"
    content = sql.read_text(encoding="utf-8").replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("""CREATE OR REPLACE FUNCTION protect_case_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.legal_hold THEN RAISE EXCEPTION 'LEGAL_HOLD_ACTIVE' USING ERRCODE = '42501'; END IF;
  RAISE EXCEPTION 'CASE_DELETE_BLOCKED' USING ERRCODE = '42501';
END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'AUDIT_APPEND_ONLY' USING ERRCODE = '42501'; END $$""")
    op.execute("REVOKE DELETE ON audit_logs, reviews FROM \"%s\"" % os.environ["DB_APP_USER"])
