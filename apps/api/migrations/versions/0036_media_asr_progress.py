"""0036 — Progreso del ASR en vivo (`media_asr_progress`).

Tabla aparte porque la transacción del pipeline mantiene bloqueada la fila de `media`
durante todo el ASR; el worker escribe aquí desde una transacción independiente para que
el monitor de procesamiento muestre el avance sin esperar al commit final.
"""
from __future__ import annotations

import os

from alembic import op

revision = "0036_media_asr_progress"
down_revision = "0035_party_roles"
branch_labels = None
depends_on = None

_SQL = """
CREATE TABLE IF NOT EXISTS media_asr_progress (
  media_id uuid PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  pct int NOT NULL DEFAULT 0 CHECK (pct BETWEEN 0 AND 100),
  detail text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE media_asr_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE media_asr_progress FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
                 AND tablename='media_asr_progress' AND policyname='tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON media_asr_progress
      USING (organization_id = current_org())
      WITH CHECK (organization_id = current_org());
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON media_asr_progress TO "{{DB_APP_USER}}";
"""


def upgrade() -> None:
    op.execute(_SQL.replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"]))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS media_asr_progress CASCADE")
