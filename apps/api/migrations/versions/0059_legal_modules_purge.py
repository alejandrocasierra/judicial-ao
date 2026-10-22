"""0059 - Permisos de purga para modulos legales."""
from __future__ import annotations

import os
import re
from pathlib import Path

from alembic import op

revision = "0059_legal_modules_purge"
down_revision = "0058_facts_graph_indexes"
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
    sql = Path(__file__).resolve().parents[2] / "db" / "sql" / "059_legal_modules_purge.sql"
    op.execute(_render(sql.read_text(encoding="utf-8")))


def downgrade() -> None:
    user = os.environ.get("DB_APP_USER", "")
    if not re.fullmatch(ALLOWED_VARS["DB_APP_USER"], user):
        raise RuntimeError("invalid value for DB_APP_USER")
    op.execute("REVOKE DELETE ON answer_audits, answer_audit_claims, answer_audit_citations, "
               "answer_audit_findings, second_opinion_runs, second_opinion_claims, "
               "second_opinion_findings, fact_revisions, legal_rule_citations, fact_positions "
               f'FROM "{user}"')
