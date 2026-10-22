"""0055 - Legal guard: politicas, auditorias y citas de normas."""
from __future__ import annotations

import os
import re
from pathlib import Path

from alembic import op

revision = "0055_legal_guard"
down_revision = "0054_chat_session_touch"
branch_labels = None
depends_on = None

ALLOWED_VARS = {"DB_APP_USER": r"^[a-z_][a-z0-9_]{0,62}$"}


def _render(sql: str) -> str:
    def sub(m):
        name = m.group(1)
        if name not in ALLOWED_VARS:
            raise RuntimeError(f"template variable not allowed: {name}")
        value = os.environ.get(name, "")
        if not re.fullmatch(ALLOWED_VARS[name], value):
            raise RuntimeError(f"invalid value for {name}")
        return value
    return re.sub(r"\{\{([A-Z_]+)\}\}", sub, sql)


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[2] / "db" / "sql" / "055_legal_guard.sql"
    op.execute(_render(sql.read_text(encoding="utf-8")))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS legal_rule_citations CASCADE")
    op.execute("DROP TABLE IF EXISTS answer_audit_findings CASCADE")
    op.execute("DROP TABLE IF EXISTS answer_audit_citations CASCADE")
    op.execute("DROP TABLE IF EXISTS answer_audit_claims CASCADE")
    op.execute("DROP TABLE IF EXISTS answer_audits CASCADE")
    op.execute("DROP TABLE IF EXISTS case_verification_settings CASCADE")
    op.execute("DROP TABLE IF EXISTS legal_guard_policies CASCADE")
    op.execute("DROP INDEX IF EXISTS ux_legal_rules_org_version")
    for col in ("official_url", "checksum", "verified_at", "verified_by", "is_active", "metadata"):
        op.execute(f"ALTER TABLE legal_rules DROP COLUMN IF EXISTS {col} CASCADE")
