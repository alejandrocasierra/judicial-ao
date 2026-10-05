"""Handler del job `indexing`: reindexa todo un caso o solo entidades estructuradas."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.db import tx
from app.services import indexing


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    actor_id = str(job.get("created_by", job.get("user_id", "")))
    # Si input_ids está vacío, reindexa el caso completo.
    input_ids = [str(x) for x in job.get("input_ids") or []]

    with tx(org_id, actor_id) as conn:
        if not input_ids:
            result = indexing.index_case(conn, org_id, case_id, actor_id)
        else:
            # Reindexa documentos/media específicos; luego reindexa entidades estructuradas.
            per_source: list[dict[str, Any]] = []
            for source_id in input_ids:
                doc = conn.execute(text("SELECT id FROM documents WHERE id = :s"), {"s": source_id}).mappings().first()
                if doc:
                    per_source.append(indexing.index_document(conn, org_id, case_id, source_id, actor_id))
                    continue
                media = conn.execute(text("SELECT id FROM media WHERE id = :s"), {"s": source_id}).mappings().first()
                if media:
                    per_source.append(indexing.index_media(conn, org_id, case_id, source_id, actor_id))
                else:
                    per_source.append({"source_id": source_id, "chunks": 0, "error": "source_not_found"})
            structured = indexing.index_structured(conn, org_id, case_id, actor_id)
            result = {"sources": per_source, "structured": structured}
    return {"implemented": True, "job_type": "indexing", "result": result}
