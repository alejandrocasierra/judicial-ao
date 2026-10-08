"""Handler del job `graph_build`: reconstruye el knowledge graph de un caso."""
from __future__ import annotations

from typing import Any

from app.core.db import tx
from app.services import entity_consolidation, graph


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    actor_id = str(job.get("created_by") or job.get("user_id") or "00000000-0000-0000-0000-000000000000")
    with tx(org_id, actor_id) as conn:
        # Antes de reconstruir el grafo: consolida entidades (dedup por nombre normalizado,
        # tipo dominante, alias fusionados). Así cualquier subida deja la BD/grafo limpios.
        consolidation = entity_consolidation.consolidate_case_entities(conn, org_id, case_id)
        result = graph.build_case_graph(conn, org_id, case_id, actor_id)
    return {"implemented": True, "job_type": "graph_build",
            "result": result, "entity_consolidation": consolidation}
