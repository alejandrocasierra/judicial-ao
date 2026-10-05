"""Handler del job `media_asr` (Fase 3)."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.db import rows, tx
from app.services import metrics
from app.services.media_pipeline import process_media

log = logging.getLogger(__name__)


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = job.get("organization_id")
    case_id = job.get("case_id")
    user_id = str(job.get("created_by") or job.get("actor_id") or "00000000-0000-0000-0000-000000000000")
    input_ids = job.get("input_ids") or []
    if not input_ids:
        return {"processed": 0, "errors": 0, "media": []}
    if not org_id or not case_id:
        # Stub estructurado para tests/unitarios que no pasan contexto completo.
        from app.workers.registry import PENDING_PHASE
        return {
            "implemented": False,
            "pending_phase": PENDING_PHASE[job.get("job_type", "media_asr")],
            "job_type": job.get("job_type", "media_asr"),
            "input_count": len(input_ids),
            "note": "stub de Fase 0: el handler real se implementa en la fase indicada",
        }
    org_id = str(org_id)
    case_id = str(case_id)

    with tx(org_id, user_id) as conn:
        media_rows = rows(conn, "SELECT id FROM media WHERE case_id = :c AND id = ANY(:ids)",
                          c=case_id, ids=[UUID(str(i)) for i in input_ids])
    media_ids = [str(r["id"]) for r in media_rows]

    processed: list[dict[str, Any]] = []
    errors = 0
    provider = get_settings().ASR_PROVIDER
    for media_id in media_ids:
        try:
            with metrics.track_stage("media_asr", org_id):
                with tx(org_id, user_id) as conn:
                    result = process_media(conn, media_id, org_id, case_id, user_id)
                    result["media_id"] = media_id
                    result["status"] = "ok"
                    processed.append(result)
            duration_ms = result.get("duration_ms") or 0
            if duration_ms:
                metrics.media_hours_processed.labels(org_id=org_id).inc(duration_ms / 3600000.0)
        except Exception as exc:  # noqa: BLE001
            log.exception("media_asr failed for media %s", media_id)
            errors += 1
            metrics.track_asr_failure(org_id, provider)
            processed.append({"media_id": media_id, "status": "error", "error": str(exc)})
            try:
                with tx(org_id, user_id) as conn:
                    from app.core.db import one
                    one(conn, "UPDATE media SET processing_status = 'FAILED' WHERE id = :m RETURNING id", m=media_id)
            except Exception:
                log.exception("no se pudo marcar media %s como FAILED", media_id)

    return {"processed": len(processed), "errors": errors, "media": processed}
