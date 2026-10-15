"""0045 — Roles de parte por lado: apoderado demandante / apoderado demandado.

Agrega estos roles de sistema a TODOS los casos existentes (idempotente). El catálogo
por caso los siembra `party_roles.ensure_defaults` para casos nuevos.
"""
from __future__ import annotations

from alembic import op

revision = "0045_party_role_sides"
down_revision = "0044_events_require_citation"
branch_labels = None
depends_on = None


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
INSERT INTO party_roles (organization_id, case_id, code, label, is_system, sort_order)
SELECT c.organization_id, c.id, v.code, v.label, true, v.sort_order
FROM cases c
CROSS JOIN (VALUES
  ('attorney_claimant', 'Apoderado parte demandante', 51),
  ('attorney_defendant', 'Apoderado parte demandada', 52),
  ('magistrado', 'Magistrado', 95)
) AS v(code, label, sort_order)
ON CONFLICT (case_id, code) DO NOTHING;
"""

DOWN = """
DELETE FROM party_roles WHERE code IN ('attorney_claimant', 'attorney_defendant');
"""


def upgrade() -> None:
    _raw(UP)


def downgrade() -> None:
    _raw(DOWN)
