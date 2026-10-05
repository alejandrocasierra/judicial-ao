"""Handler del job `document_classification` (Fase 2)."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core.db import rows, tx
from app.services.document_pipeline import classify_and_index_document

log = logging.getLogger(__name__)


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    user_id = str(job.get("created_by") or job.get("actor_id") or "00000000-0000-0000-0000-000000000000")
    input_ids = job.get("input_ids") or []
    if not input_ids:
        return {"classified": 0, "documents": []}

    with tx(org_id, user_id) as conn:
        doc_rows = rows(conn,
                        "SELECT id FROM documents WHERE case_id = :c AND id = ANY(:ids) "
                        "AND processing_status IN ('OCR_COMPLETE','REVIEW_REQUIRED')",
                        c=case_id, ids=[UUID(str(i)) for i in input_ids])
    document_ids = [str(r["id"]) for r in doc_rows]

    results: list[dict[str, Any]] = []
    for document_id in document_ids:
        try:
            with tx(org_id, user_id) as conn:
                result = classify_and_index_document(conn, document_id, case_id)
                result["status"] = "ok"
                results.append(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("document_classification failed for document %s", document_id)
            results.append({"document_id": document_id, "status": "error", "error": str(exc)})

    return {"classified": len(results), "documents": results}
