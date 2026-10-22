"""0064 - Respuesta retenida y liberación humana auditada."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0064_legal_hold_release"
down_revision = "0063_legal_gate_semantics"
branch_labels = None
depends_on = None


def _sql(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / "db" / "sql" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    op.execute(_sql("064_legal_hold_release.sql"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_publication_blocked")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS publication_block_reason")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS publication_blocked")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS held_occurrences")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS held_citations")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS held_answer")
