"""0027 — Confianza OCR a 100% por página/modo (decisión de producto).

Normaliza la confianza existente a 1.0 y quita la marca "Revisar" del OCR. El %
sólo bajará cuando una persona edite contenido real.
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0027_ocr_confidence_reset"
down_revision = "0026_graph_speaker_node"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "032_ocr_confidence_reset.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    # La confianza del motor no se conserva: no hay nada que restaurar.
    pass
