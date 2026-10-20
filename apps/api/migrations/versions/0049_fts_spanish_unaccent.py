"""0049 — FTS español + unaccent en columnas generadas nuevas."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0049_fts_spanish_unaccent"
down_revision = "0048_chat_history_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "049_fts_spanish_unaccent.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_search_tsv")
    op.execute("DROP INDEX IF EXISTS ix_segments_search_tsv")
    op.execute("DROP INDEX IF EXISTS ix_pages_search_tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS search_tsv")
    op.execute("ALTER TABLE transcript_segments DROP COLUMN IF EXISTS search_tsv")
    op.execute("ALTER TABLE document_pages DROP COLUMN IF EXISTS search_tsv")
    op.execute("DROP FUNCTION IF EXISTS public.immutable_unaccent(text)")
