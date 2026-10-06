"""Handler del job `legal_extraction` (Fase 4).

Procesa documentos o media ya transcriptos, extrae entidades, claims, eventos y
decisiones con LLM, valida contra schemas y persiste en el esquema de conocimiento.
Al final detecta contradicciones entre los claims del caso.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.db import one, tx
from app.services.legal_extraction import (detect_contradictions, extract_claims, extract_decisions,
                                            extract_entities, extract_events, extract_procedural_events,
                                            link_evidence)

log = logging.getLogger(__name__)


def _resolve_source(conn: Any, case_id: str, source_id: str) -> tuple[str, str] | None:
    doc = one(conn, "SELECT id FROM documents WHERE case_id = :c AND id = :id",
              c=case_id, id=source_id)
    if doc:
        return ("document", str(doc["id"]))
    med = one(conn, "SELECT id FROM media WHERE case_id = :c AND id = :id",
              c=case_id, id=source_id)
    if med:
        return ("media", str(med["id"]))
    return None


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    user_id = str(job.get("created_by") or job.get("actor_id") or "00000000-0000-0000-0000-000000000000")
    input_ids = job.get("input_ids") or []
    if not input_ids:
        return {"processed": 0, "results": []}

    results: list[dict[str, Any]] = []
    for raw_id in input_ids:
        source_id = str(raw_id)
        result: dict[str, Any] = {"source_id": source_id, "status": "ok"}
        try:
            with tx(org_id, user_id) as conn:
                source = _resolve_source(conn, case_id, source_id)
                if not source:
                    result.update({"status": "error", "error": "source_not_found"})
                    results.append(result)
                    continue
                source_type, resolved_id = source
                result["source_type"] = source_type
                result["entities"] = extract_entities(conn, org_id, case_id, source_type, resolved_id, user_id)
                result["claims"] = extract_claims(conn, org_id, case_id, source_type, resolved_id, user_id)
                result["events"] = extract_events(conn, org_id, case_id, source_type, resolved_id, user_id)
                result["procedural_events"] = extract_procedural_events(conn, org_id, case_id, source_type, resolved_id, user_id)
                result["decisions"] = extract_decisions(conn, org_id, case_id, source_type, resolved_id, user_id)
                result["evidence_links"] = link_evidence(conn, org_id, case_id, source_type, resolved_id, user_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("legal_extraction failed for source %s", source_id)
            result.update({"status": "error", "error": str(exc)})
        results.append(result)

    # Detección de contradicciones se ejecuta una vez por caso, después de procesar todas las fuentes.
    contradictions: dict[str, Any] | None = None
    if any(r.get("status") == "ok" for r in results):
        try:
            with tx(org_id, user_id) as conn:
                contradictions = detect_contradictions(conn, org_id, case_id, user_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("contradiction detection failed for case %s", case_id)
            contradictions = {"status": "error", "error": str(exc)}

    # Encadena la reconstrucción del grafo con los nodos recién extraídos.
    if any(r.get("status") == "ok" for r in results):
        try:
            from app.workers.handlers.file_ingest import enqueue_graph_refresh
            enqueue_graph_refresh(org_id, case_id, user_id)
        except Exception:  # noqa: BLE001
            log.exception("no se pudo encolar graph_build tras legal_extraction (caso %s)", case_id)

    return {"processed": len(results), "results": results, "contradictions": contradictions}
