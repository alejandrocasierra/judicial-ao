"""0019 — nombre visible del hablante (identificación visual de Teams)."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0019_speaker_display_name"
down_revision = "0018_ocr_progress"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "024_speaker_display_name.sql"


def upgrade() -> None:
    op.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE speakers DROP COLUMN IF EXISTS display_name")
