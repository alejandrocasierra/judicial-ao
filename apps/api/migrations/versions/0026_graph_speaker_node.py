"""0026 — Hablantes (diarización) como nodos del knowledge graph.

Amplía los CHECK de graph_nodes.node_type (Speaker) y graph_edges.edge_type
(HAS_SPEAKER, IS_PARTY) para que renombrar un hablante se refleje al reconstruir
el grafo.
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0026_graph_speaker_node"
down_revision = "0025_model_base_url"
branch_labels = None
depends_on = None

_NODE_TYPES_OLD = ("'Case','Person','Organization','Document','Claim','Fact','Evidence','Event',"
                   "'LegalRule','Decision','Issue'")
_EDGE_TYPES_OLD = ("'ASSERTS','SUPPORTS','CONTRADICTS','REFUTES','CITES','PARTICIPATED_IN',"
                   "'TESTIFIED_IN','DECIDES','APPLIES','DERIVED_FROM','MENTIONS','ABOUT'")


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[4] / "apps" / "api" / "db" / "sql" / "031_graph_speaker_node.sql"
    op.execute(sql.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DELETE FROM graph_edges WHERE edge_type IN ('HAS_SPEAKER','IS_PARTY')")
    op.execute("DELETE FROM graph_nodes WHERE node_type = 'Speaker'")
    op.execute("ALTER TABLE graph_edges DROP CONSTRAINT IF EXISTS graph_edges_edge_type_check")
    op.execute(f"ALTER TABLE graph_edges ADD CONSTRAINT graph_edges_edge_type_check CHECK (edge_type IN ({_EDGE_TYPES_OLD}))")
    op.execute("ALTER TABLE graph_nodes DROP CONSTRAINT IF EXISTS graph_nodes_node_type_check")
    op.execute(f"ALTER TABLE graph_nodes ADD CONSTRAINT graph_nodes_node_type_check CHECK (node_type IN ({_NODE_TYPES_OLD}))")
