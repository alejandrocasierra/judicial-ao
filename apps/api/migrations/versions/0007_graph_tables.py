"""Knowledge Graph: tablas graph_nodes y graph_edges.

{{VAR}} se reemplaza con variables de entorno (misma lista blanca que 0001).
"""
import os
import re

from alembic import op

revision = "0007_graph_tables"
down_revision = "0005_segments_reprocess"
branch_labels = None
depends_on = None

ALLOWED_VARS = {"DB_APP_USER": r"^[a-z_][a-z0-9_]{0,62}$"}


def render(sql: str) -> str:
    def sub(m):
        name = m.group(1)
        if name not in ALLOWED_VARS:
            raise RuntimeError(f"template variable not allowed: {name}")
        val = os.environ.get(name, "")
        if not re.fullmatch(ALLOWED_VARS[name], val):
            raise RuntimeError(f"invalid value for {name}")
        return val
    return re.sub(r"\{\{([A-Z_]+)\}\}", sub, sql)


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
CREATE TABLE IF NOT EXISTS graph_nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  node_type text NOT NULL CHECK (node_type IN ('Case','Person','Organization','Document','Claim','Fact','Evidence','Event','LegalRule','Decision','Issue')),
  source_table text,
  source_id uuid,
  label text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, source_table, source_id, node_type)
);

CREATE TABLE IF NOT EXISTS graph_edges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  source_node_id uuid NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
  target_node_id uuid NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
  edge_type text NOT NULL CHECK (edge_type IN ('ASSERTS','SUPPORTS','CONTRADICTS','REFUTES','CITES','PARTICIPATED_IN','TESTIFIED_IN','DECIDES','APPLIES','DERIVED_FROM','MENTIONS','ABOUT')),
  provenance text NOT NULL CHECK (provenance IN ('EXTRACTED','INFERRED','AMBIGUOUS')),
  confidence numeric(4,3) CHECK (confidence >= 0 AND confidence <= 1),
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (source_node_id <> target_node_id)
);

CREATE INDEX IF NOT EXISTS ix_graph_nodes_case_type ON graph_nodes(case_id, node_type);
CREATE INDEX IF NOT EXISTS ix_graph_nodes_source ON graph_nodes(case_id, source_table, source_id);
CREATE INDEX IF NOT EXISTS ix_graph_edges_case ON graph_edges(case_id);
CREATE INDEX IF NOT EXISTS ix_graph_edges_source ON graph_edges(source_node_id);
CREATE INDEX IF NOT EXISTS ix_graph_edges_target ON graph_edges(target_node_id);
CREATE INDEX IF NOT EXISTS ix_graph_edges_type ON graph_edges(case_id, edge_type);

ALTER TABLE graph_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_nodes FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='graph_nodes' AND policyname='tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON graph_nodes USING (organization_id = current_org()) WITH CHECK (organization_id = current_org());
  END IF;
END $$;

ALTER TABLE graph_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_edges FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='graph_edges' AND policyname='tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON graph_edges USING (organization_id = current_org()) WITH CHECK (organization_id = current_org());
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON graph_nodes, graph_edges TO "{{DB_APP_USER}}";
"""

DOWN = """
DROP TABLE IF EXISTS graph_edges CASCADE;
DROP TABLE IF EXISTS graph_nodes CASCADE;
"""


def upgrade() -> None:
    _raw(render(UP))


def downgrade() -> None:
    _raw(render(DOWN))
