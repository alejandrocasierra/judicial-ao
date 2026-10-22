"""0062 - Invariantes de estado de prueba en facts.

La base local ya tenía aplicada esta revisión, pero el archivo no estaba en el
repositorio. Se reconstruyó desde `pg_get_functiondef` para que una base limpia
y la local converjan en el mismo head sin duplicar triggers.
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0062_legal_p1_invariants"
down_revision = "0061_second_opinion_worker"
branch_labels = None
depends_on = None


def _sql(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / "db" / "sql" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    op.execute(_sql("062_legal_p1_invariants.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_facts_proof_invariant ON facts")
    op.execute("DROP FUNCTION IF EXISTS fact_proof_invariant()")
