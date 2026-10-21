"""0051 — Instrumentación del router de intención (decisión completa + feedback)."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0051_intent_instrumentation"
down_revision = "0050_party_roles_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[2] / "db" / "sql" / "051_intent_instrumentation.sql"
    content = sql.read_text(encoding="utf-8").replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"])
    op.execute(content)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS intent_feedback")
    op.execute("ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_intent_confidence_range")
    for column in ("intent_confidence", "intent_reason", "intent_source", "router_version"):
        op.execute(f"ALTER TABLE chat_messages DROP COLUMN IF EXISTS {column}")
