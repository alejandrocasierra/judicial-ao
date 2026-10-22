"""0057 - Hechos por parte y estado de prueba."""
from __future__ import annotations

import os
import re
from pathlib import Path

from alembic import op

revision = "0057_party_facts"
down_revision = "0056_second_opinion"
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
    sql = Path(__file__).resolve().parents[2] / "db" / "sql" / "057_party_facts.sql"
    op.execute(_render(sql.read_text(encoding="utf-8")))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fact_revisions CASCADE")
    op.execute("DROP TABLE IF EXISTS fact_positions CASCADE")
    op.execute("ALTER TABLE evidence_links DROP COLUMN IF EXISTS citation_id CASCADE")
    op.execute("ALTER TABLE evidence_links DROP COLUMN IF EXISTS added_by CASCADE")
    op.execute("ALTER TABLE evidence_links DROP COLUMN IF EXISTS added_at CASCADE")
    for col in ("is_proven", "proof_status", "proof_basis", "proven_by_decision_id",
                "proven_at", "proven_by", "issue_id", "fact_code", "created_at"):
        op.execute(f"ALTER TABLE facts DROP COLUMN IF EXISTS {col} CASCADE")
