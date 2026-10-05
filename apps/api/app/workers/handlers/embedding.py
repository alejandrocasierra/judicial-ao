"""Handler del job `embedding`: genera chunks + embeddings para documentos/media."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.db import tx
from app.services import indexing


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    actor_id = str(job.get("created_by", job.get("user_id", "")))
    input_ids = [str(x) for x in job.get("input_ids") or []]

    results: list[dict[str, Any]] = []
    with tx(org_id, actor_id) as conn:
        for source_id in input_ids:
            # Decide si es documento o media por existencia en cada tabla.
            doc = conn.execute(text("SELECT id FROM documents WHERE id = :s"), {"s": source_id}).mappings().first()
            if doc:
                results.append(indexing.index_document(conn, org_id, case_id, source_id, actor_id))
                continue
            media = conn.execute(text("SELECT id FROM media WHERE id = :s"), {"s": source_id}).mappings().first()
            if media:
                results.append(indexing.index_media(conn, org_id, case_id, source_id, actor_id))
            else:
                results.append({"source_id": source_id, "chunks": 0, "error": "source_not_found"})
    return {"implemented": True, "job_type": "embedding", "results": results}
