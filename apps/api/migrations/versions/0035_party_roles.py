"""0035 — Partes: catálogo de roles por caso (`party_roles`) y `parties.role` libre.

Antes `parties.role` estaba limitado por un CHECK a 12 códigos fijos. Ahora el rol es
un texto libre que referencia el catálogo por caso `party_roles` (crear/renombrar/eliminar).
"""
from __future__ import annotations

import os

from alembic import op

revision = "0035_party_roles"
down_revision = "0034_user_version"
branch_labels = None
depends_on = None

_SQL = """
-- Rol de parte: texto libre (el catálogo por caso manda el CHECK).
ALTER TABLE parties DROP CONSTRAINT IF EXISTS parties_role_check;

-- Catálogo de roles de parte POR CASO (crear/renombrar/eliminar).
CREATE TABLE IF NOT EXISTS party_roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  code text NOT NULL,
  label text NOT NULL,
  is_system boolean NOT NULL DEFAULT false,
  sort_order integer NOT NULL DEFAULT 100,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, code)
);
CREATE INDEX IF NOT EXISTS ix_party_roles_case ON party_roles (case_id);

ALTER TABLE party_roles ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
                   AND tablename='party_roles' AND policyname='tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON party_roles
      USING (organization_id = current_org()) WITH CHECK (organization_id = current_org());
  END IF;
END $$;
GRANT SELECT, INSERT, UPDATE, DELETE ON party_roles TO "{{DB_APP_USER}}";
"""


def upgrade() -> None:
    op.execute(_SQL.replace("{{DB_APP_USER}}", os.environ["DB_APP_USER"]))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS party_roles CASCADE")
    op.execute("""ALTER TABLE parties ADD CONSTRAINT parties_role_check CHECK (role IN
        ('claimant','defendant','plaintiff','respondent','appellant','appellee',
         'witness','expert','judge','attorney','representative','third_party'))""")
