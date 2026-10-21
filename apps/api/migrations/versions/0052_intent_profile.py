"""0052 — Perfil de intención por usuario (ejemplos + estado del aprendizaje)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from alembic import op

revision = "0052_intent_profile"
down_revision = "0051_intent_instrumentation"
branch_labels = None
depends_on = None

ALLOWED_VARS = {"DB_APP_USER": r"^[a-z_][a-z0-9_]{0,62}$", "EMBEDDING_DIMENSIONS": r"^\d{1,5}$"}


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
    sql = Path(__file__).resolve().parents[2] / "db" / "sql" / "052_intent_profile.sql"
    op.execute(_render(sql.read_text(encoding="utf-8")))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS intent_profile")
    op.execute("DROP TABLE IF EXISTS intent_exemplar")
