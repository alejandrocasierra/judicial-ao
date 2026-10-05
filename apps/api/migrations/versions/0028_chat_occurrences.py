"""0028 — Persiste las "apariciones" (occurrences) de los mensajes del chat.

Las citas ya vivían en chat_messages.citations; el bloque "Aparece en N ubicaciones"
no se guardaba y se perdía al cambiar de conversación o enviar otro mensaje.
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0028_chat_occurrences"
down_revision = "0027_ocr_confidence_reset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "033_chat_occurrences.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS occurrences")
