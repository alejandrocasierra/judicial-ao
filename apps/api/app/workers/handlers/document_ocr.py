"""Handler del job `document_ocr` (Fase 2)."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.services import metrics
from app.services.document_pipeline import process_document

log = logging.getLogger(__name__)


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    user_id = str(job.get("created_by") or job.get("actor_id") or "00000000-0000-0000-0000-000000000000")
    input_ids = job.get("input_ids") or []
    if not input_ids:
        return {"processed": 0, "errors": 0, "documents": []}

    # Filtra solo documentos (input_ids puede incluir media).
    with tx(org_id, user_id) as conn:
        doc_rows = rows(conn, "SELECT id FROM documents WHERE case_id = :c AND id = ANY(:ids)",
                        c=case_id, ids=[UUID(str(i)) for i in input_ids])
    document_ids = [str(r["id"]) for r in doc_rows]

    processed: list[dict[str, Any]] = []
    errors = 0
    provider = get_settings().OCR_PROVIDER
    for document_id in document_ids:
        try:
            with metrics.track_stage("document_ocr", org_id):
                with tx(org_id, user_id) as conn:
                    result = process_document(conn, document_id, org_id, case_id, user_id)
                    result["document_id"] = document_id
                    result["status"] = "ok"
                    processed.append(result)
            metrics.docs_processed.labels(org_id=org_id, status="ok").inc()
            pages = result.get("pages_processed") or result.get("pages") or 0
            if pages:
                metrics.pages_processed.labels(org_id=org_id, status="ok").inc(pages)
        except Exception as exc:  # noqa: BLE001
            log.exception("document_ocr failed for document %s", document_id)
            errors += 1
            metrics.docs_processed.labels(org_id=org_id, status="error").inc()
            metrics.track_ocr_failure(org_id, provider)
            processed.append({"document_id": document_id, "status": "error", "error": str(exc)})
            # Marca el documento como FAILED para visibilidad; no propagamos para no
            # cancelar el procesamiento de los demás documentos del caso.
            try:
                with tx(org_id, user_id) as conn:
                    one(conn, "UPDATE documents SET processing_status = 'FAILED' WHERE id = :d RETURNING id", d=document_id)
            except Exception:
                log.exception("no se pudo marcar document %s como FAILED", document_id)

    return {"processed": len(processed), "errors": errors, "documents": processed}
