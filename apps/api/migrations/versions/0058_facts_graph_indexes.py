"""0058 - RLS, indices, triggers de hechos y tipo de chunk fact."""
from __future__ import annotations

import os
import re
from pathlib import Path

from alembic import op

revision = "0058_facts_graph_indexes"
down_revision = "0057_party_facts"
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
    sql = Path(__file__).resolve().parents[2] / "db" / "sql" / "058_facts_graph_indexes.sql"
    op.execute(_render(sql.read_text(encoding="utf-8")))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_facts_proof_invariant ON facts")
    op.execute("DROP FUNCTION IF EXISTS fact_proof_invariant()")
    op.execute("DELETE FROM chunks WHERE chunk_type = 'fact'")
    op.execute("ALTER TABLE chunks DROP CONSTRAINT IF EXISTS chunks_chunk_type_check")
    op.execute("""ALTER TABLE chunks ADD CONSTRAINT chunks_chunk_type_check
      CHECK (chunk_type IN ('document_section','paragraph','claim','testimony_segment',
                            'timeline_event','legal_rule','evidence_description','decision_reasoning'))""")
    op.execute("DROP INDEX IF EXISTS ix_facts_case_proof")
    op.execute("DROP INDEX IF EXISTS ix_facts_case_proven")
    op.execute("DROP INDEX IF EXISTS ix_facts_case_issue")
    op.execute("DROP INDEX IF EXISTS ix_chunks_metadata_gin")
    op.execute("DROP INDEX IF EXISTS ix_chunks_case_type")
    op.execute("DROP INDEX IF EXISTS ix_graph_edges_case_type_target")
    # Las policies y grants desaparecen al eliminar las tablas en 0057/0056/0055.
