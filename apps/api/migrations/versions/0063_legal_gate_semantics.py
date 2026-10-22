"""0063 - Gate semantico, precios de modelo y metadatos de segunda opinion."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0063_legal_gate_semantics"
down_revision = "0062_legal_p1_invariants"
branch_labels = None
depends_on = None


def _sql(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / "db" / "sql" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    op.execute(_sql("063_legal_gate_semantics.sql"))


def downgrade() -> None:
    op.execute("ALTER TABLE second_opinion_runs DROP CONSTRAINT IF EXISTS second_opinion_comparison_mode_check")
    op.execute("ALTER TABLE second_opinion_runs DROP CONSTRAINT IF EXISTS second_opinion_cost_source_check")
    op.execute("ALTER TABLE second_opinion_runs DROP COLUMN IF EXISTS comparison_error")
    op.execute("ALTER TABLE second_opinion_runs DROP COLUMN IF EXISTS comparison_mode")
    op.execute("ALTER TABLE second_opinion_runs DROP COLUMN IF EXISTS cost_source")
    op.execute("ALTER TABLE ai_models DROP CONSTRAINT IF EXISTS ai_models_price_nonnegative")
    op.execute("ALTER TABLE ai_models DROP COLUMN IF EXISTS output_price_per_mtok")
    op.execute("ALTER TABLE ai_models DROP COLUMN IF EXISTS input_price_per_mtok")
