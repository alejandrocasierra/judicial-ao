"""0056 - Segunda opinion por proceso/demanda."""
from __future__ import annotations

import os
import re
from pathlib import Path

from alembic import op

revision = "0056_second_opinion"
down_revision = "0055_legal_guard"
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
    sql = Path(__file__).resolve().parents[2] / "db" / "sql" / "056_second_opinion.sql"
    op.execute(_render(sql.read_text(encoding="utf-8")))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS second_opinion_findings CASCADE")
    op.execute("DROP TABLE IF EXISTS second_opinion_claims CASCADE")
    op.execute("DROP TABLE IF EXISTS second_opinion_runs CASCADE")
