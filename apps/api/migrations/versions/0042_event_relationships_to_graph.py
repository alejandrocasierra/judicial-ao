"""0042 — Proyectar event_relationships al knowledge graph general.

- Agrega provenance a event_relationships (EXTRACTED/INFERRED/AMBIGUOUS).
- Extiende graph_edges.edge_type con PRECEDES, CAUSES, APPEALS, RESPONDS_TO, REFERS_TO.
"""
from __future__ import annotations

from alembic import op

revision = "0042_event_relationships_graph"
down_revision = "0041_event_dates"
branch_labels = None
depends_on = None


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
ALTER TABLE event_relationships
    ADD COLUMN IF NOT EXISTS provenance text NOT NULL DEFAULT 'INFERRED'
        CHECK (provenance IN ('EXTRACTED','INFERRED','AMBIGUOUS'));

ALTER TABLE graph_edges
    DROP CONSTRAINT IF EXISTS graph_edges_edge_type_check;

ALTER TABLE graph_edges
    ADD CONSTRAINT graph_edges_edge_type_check
        CHECK (edge_type IN (
            'ASSERTS','SUPPORTS','CONTRADICTS','REFUTES','CITES',
            'PARTICIPATED_IN','TESTIFIED_IN','DECIDES','APPLIES',
            'DERIVED_FROM','MENTIONS','ABOUT','HAS_SPEAKER','IS_PARTY',
            'PRECEDES','CAUSES','APPEALS','RESPONDS_TO','REFERS_TO'
        ));
"""

DOWN = """
ALTER TABLE graph_edges
    DROP CONSTRAINT IF EXISTS graph_edges_edge_type_check;

ALTER TABLE graph_edges
    ADD CONSTRAINT graph_edges_edge_type_check
        CHECK (edge_type IN (
            'ASSERTS','SUPPORTS','CONTRADICTS','REFUTES','CITES',
            'PARTICIPATED_IN','TESTIFIED_IN','DECIDES','APPLIES',
            'DERIVED_FROM','MENTIONS','ABOUT','HAS_SPEAKER','IS_PARTY'
        ));

ALTER TABLE event_relationships DROP COLUMN IF EXISTS provenance;
"""


def upgrade() -> None:
    _raw(UP)


def downgrade() -> None:
    _raw(DOWN)
