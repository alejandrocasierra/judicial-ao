"""0020 — deduplicación por (contenido + nombre) en vez de sólo por contenido."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0020_dedup_by_filename"
down_revision = "0019_speaker_display_name"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "025_dedup_by_filename.sql"


def upgrade() -> None:
    op.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE documents ADD CONSTRAINT documents_case_id_sha256_key UNIQUE (case_id, sha256)")
    op.execute("ALTER TABLE media ADD CONSTRAINT media_case_id_sha256_key UNIQUE (case_id, sha256)")
