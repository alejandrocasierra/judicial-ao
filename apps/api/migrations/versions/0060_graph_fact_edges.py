"""0060 - Tipos de arista del grafo para hechos."""
from __future__ import annotations

import os
import re
from pathlib import Path

from alembic import op

revision = "0060_graph_fact_edges"
down_revision = "0059_legal_modules_purge"
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
    sql = Path(__file__).resolve().parents[2] / "db" / "sql" / "060_graph_fact_edges.sql"
    op.execute(_render(sql.read_text(encoding="utf-8")))


def downgrade() -> None:
    op.execute("ALTER TABLE graph_edges DROP CONSTRAINT IF EXISTS graph_edges_edge_type_check")
    op.execute("""ALTER TABLE graph_edges ADD CONSTRAINT graph_edges_edge_type_check
      CHECK (edge_type IN ('ASSERTS','SUPPORTS','CONTRADICTS','REFUTES','CITES','PARTICIPATED_IN',
                           'TESTIFIED_IN','DECIDES','APPLIES','DERIVED_FROM','MENTIONS','ABOUT',
                           'HAS_SPEAKER','IS_PARTY','PRECEDES','CAUSES','APPEALS','RESPONDS_TO','REFERS_TO'))""")
