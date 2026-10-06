"""Handler del job `procedural_links`: Process Graph de la línea de tiempo procesal.

Ejecuta en el worker lo que el botón «Causas con IA» hace en segundo plano:
- `link_events` (reglas: causa/refiere a/precede/apela/responde) y `review_events`
  (marcas de revisión: duplicado, fecha_inconsistente, sin_fuente_real, sin_fecha).
- `propose_relations_llm`: segundo pase con el modelo de IA de la organización que
  propone relaciones causales (consume tokens y puede tardar minutos → por eso no
  se hace en la petición HTTP).
"""
from __future__ import annotations

from typing import Any

from app.core.db import tx
from app.services import procedural_graph


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    actor_id = str(job.get("created_by") or job.get("user_id") or "00000000-0000-0000-0000-000000000000")
    with tx(org_id, actor_id) as conn:
        linked = procedural_graph.link_events(conn, org_id, case_id)
        reviewed = procedural_graph.review_events(conn, org_id, case_id)
        ai = procedural_graph.propose_relations_llm(conn, org_id, case_id, actor_id)
    return {"implemented": True, "job_type": "procedural_links",
            "linked": linked, "reviewed": reviewed, "ai_links": ai}
