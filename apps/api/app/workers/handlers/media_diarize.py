"""Handler del job `media_diarize` (etapa 2 de audio/video).

La diarización (pyannote) e identificación visual cargan ~2-3 GB de modelos +
el audio completo. Ejecutarlas en el MISMO proceso que Whisper (etapa 1) agotaba
la RAM del servidor (SIGKILL/OOM). Por eso se corren aquí, en su propio job y
proceso, y solo se actualizan los `transcript_segments` que dejó el ASR.

Además reindexa los chunks del medio (para que las citas/embeddings lleven los
nombres de hablante resueltos) y encola el refresco del knowledge graph.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.services import metrics
from app.services.media_pipeline import apply_diarization
from app.services.storage import incoming_dir, key_from_uri, storage
from app.workers.dispatcher import create_job, enqueue_if_pending

log = logging.getLogger(__name__)


def _upload_original_and_cleanup(mrow: dict | None) -> None:
    """Sube el original del medio a GCS desde la copia local y la libera.

    Se hace al final de la etapa 2 (tras commitear hablantes) para que la subida
    —lenta con internet lento— no retrase la diarización."""
    if not mrow or not mrow.get("sha256") or not mrow.get("storage_uri"):
        return
    local = incoming_dir() / str(mrow["sha256"])
    if local.exists():
        storage().put_file(key_from_uri(str(mrow["storage_uri"])), local)
        local.unlink(missing_ok=True)


def enqueue_media_diarization(org_id: str, case_id: str, actor_id: str, media_ids: list[str]) -> None:
    """Encola (uno por medio) la etapa de diarización, evitando duplicados activos.

    La clave de idempotencia lleva un UUID único para PERMITIR reprocesar un medio
    ya diarizado, pero se omite si ya hay un job de diarización en curso para él."""
    if not media_ids:
        return
    s = get_settings()
    for media_id in media_ids:
        with tx(org_id, actor_id) as conn:
            active = one(conn, """
                SELECT id FROM jobs
                WHERE organization_id = :o AND job_type = 'media_diarize'
                  AND status IN ('QUEUED', 'RUNNING', 'RETRYING')
                  AND input_ids @> ARRAY[:m]::uuid[]
                LIMIT 1
            """, o=org_id, m=media_id)
        if active:
            continue
        with tx(org_id, actor_id) as conn:
            job = create_job(conn, org_id=org_id, case_id=case_id, job_type="media_diarize",
                             input_ids=[media_id], actor_id=actor_id,
                             key_parts=["media_diarize", case_id, media_id, str(uuid4())],
                             pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
        try:
            enqueue_if_pending(job, org_id, actor_id)
        except Exception:  # broker caído: el job queda QUEUED y lo recoge el sweeper
            log.exception("no se pudo encolar media_diarize (media %s)", media_id)


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    actor_id = str(job.get("created_by") or "00000000-0000-0000-0000-000000000000")
    input_ids = [str(x) for x in job.get("input_ids") or []]
    if not input_ids:
        return {"processed": 0, "errors": 0, "media": []}

    with tx(org_id, actor_id) as conn:
        media_rows = rows(conn, "SELECT id FROM media WHERE case_id = :c AND id = ANY(:ids)",
                          c=case_id, ids=[UUID(i) for i in input_ids])
    media_ids = [str(r["id"]) for r in media_rows]

    processed: list[dict[str, Any]] = []
    errors = 0
    for media_id in media_ids:
        try:
            with metrics.track_stage("media_diarize", org_id):
                with tx(org_id, actor_id) as conn:
                    result = apply_diarization(conn, media_id, org_id, case_id, actor_id)
                # Reindexa los chunks con los nombres de hablante ya resueltos.
                try:
                    from app.services import indexing
                    with tx(org_id, actor_id) as conn:
                        indexing.index_media(conn, org_id, case_id, media_id, actor_id)
                except Exception:  # noqa: BLE001
                    log.exception("reindexado tras diarización falló (media %s)", media_id)
                # Sube el original a GCS y libera la copia local (última etapa del medio).
                # Después del commit de los hablantes: una subida lenta no retrasa nada.
                try:
                    with tx(org_id, actor_id) as conn:
                        mrow = one(conn, "SELECT sha256, storage_uri FROM media WHERE id = :m", m=media_id)
                    _upload_original_and_cleanup(mrow)
                except Exception:  # noqa: BLE001
                    log.exception("no se pudo subir el original del medio %s", media_id)
            result["media_id"] = media_id
            result["status"] = "ok"
            processed.append(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("media_diarize falló para %s", media_id)
            errors += 1
            processed.append({"media_id": media_id, "status": "error", "error": str(exc)})
            try:
                with tx(org_id, actor_id) as conn:
                    one(conn, "UPDATE media SET processing_status = 'ASR_COMPLETE' WHERE id = :m RETURNING id",
                        m=media_id)
            except Exception:  # noqa: BLE001
                log.exception("no se pudo restaurar el estado de %s", media_id)

    if processed:
        try:
            from app.workers.handlers.file_ingest import enqueue_graph_refresh
            enqueue_graph_refresh(org_id, case_id, actor_id, correction=True)
        except Exception:  # noqa: BLE001
            log.exception("no se pudo encolar graph_build tras la diarización (caso %s)", case_id)

    # No tragarse los errores: si algún medio falló, el job debe verse como fallido
    # (antes marcaba SUCCEEDED aunque la diarización hubiera explotado).
    if errors:
        raise RuntimeError(f"media_diarize: fallaron {errors} medio(s); ver logs del worker")
    return {"processed": len(processed), "errors": errors, "media": processed}
