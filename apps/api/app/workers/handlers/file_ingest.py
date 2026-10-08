"""Handler del job `file_ingest` (módulo Procesos).

Cadena completa por archivo subido desde el gestor de archivos:
  - document (PDF): OCR por página + folios + clasificación (`process_document`)
    y después chunks + embeddings en pgvector (`indexing.index_document`).
  - media (MP4): ASR + diarización + hablantes (`process_media`) y después
    chunks + embeddings (`indexing.index_media`).

Al terminar el lote del job se encola una reconstrucción del knowledge graph
del caso, deduplicada por ventana de 10 minutos vía idempotency_key (una
subida masiva genera N jobs file_ingest pero como mucho un graph_build por
ventana; el último en ejecutarse deja el grafo al día).
"""
from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.services import indexing, metrics
from app.services.document_pipeline import process_document
from app.services.media_pipeline import process_media
from app.services.storage import incoming_dir, key_from_uri, storage
from app.workers.dispatcher import create_job, enqueue_if_pending

log = logging.getLogger(__name__)

# Ventana de deduplicación del graph_build posterior a la ingesta (segundos).
GRAPH_REFRESH_WINDOW_SECONDS = 600
# Las correcciones humanas agrupan su reconstrucción en ventanas cortas: así una
# edición no queda suprimida por un graph_build de ingesta reciente, pero una ráfaga
# de ediciones genera como mucho un rebuild cada CORRECTION_GROUP_SECONDS.
CORRECTION_GROUP_SECONDS = 30


def enqueue_graph_refresh(org_id: str, case_id: str, actor_id: str, *, correction: bool = False) -> None:
    """Encola una reconstrucción del knowledge graph del caso, deduplicada por
    ventana de 10 minutos vía idempotency_key (N correcciones/ingestas generan
    como mucho un graph_build por ventana; el último deja el grafo al día).

    `correction=True` (ediciones humanas: renombrar un hablante o reasignar quién
    dijo un segmento) usa una clave con ventana corta para no quedar suprimida por
    un graph_build de ingesta reciente."""
    s = get_settings()
    window = int(time.time() // GRAPH_REFRESH_WINDOW_SECONDS)
    key_parts = ["graph_build", "file_ingest", case_id, window]
    if correction:
        key_parts.append(f"correction:{int(time.time() // CORRECTION_GROUP_SECONDS)}")
    with tx(org_id, actor_id) as conn:
        job = create_job(conn, org_id=org_id, case_id=case_id, job_type="graph_build",
                         input_ids=[], actor_id=actor_id,
                         key_parts=key_parts,
                         pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
    try:
        enqueue_if_pending(job, org_id, actor_id)
    except Exception:  # broker caído: el job queda QUEUED y lo recoge el sweeper
        log.exception("no se pudo encolar graph_build tras file_ingest (caso %s)", case_id)


def enqueue_legal_extraction(org_id: str, case_id: str, actor_id: str, source_ids: list[str]) -> None:
    """Encola la extracción legal (entidades/claims/facts) de las fuentes recién
    procesadas. Al terminar, su handler encadena el graph_build del caso."""
    if not source_ids:
        return
    s = get_settings()
    with tx(org_id, actor_id) as conn:
        job = create_job(conn, org_id=org_id, case_id=case_id, job_type="legal_extraction",
                         input_ids=source_ids, actor_id=actor_id,
                         key_parts=["legal_extraction", case_id, sorted(source_ids)],
                         pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
    try:
        enqueue_if_pending(job, org_id, actor_id)
    except Exception:  # broker caído: el job queda QUEUED y lo recoge el sweeper
        log.exception("no se pudo encolar legal_extraction tras file_ingest (caso %s)", case_id)


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    actor_id = str(job.get("created_by") or "00000000-0000-0000-0000-000000000000")
    input_ids = [str(x) for x in job.get("input_ids") or []]
    if not input_ids:
        return {"processed": 0, "errors": 0, "files": []}

    # Clasifica cada entrada: documento (OCR) o media (ASR).
    with tx(org_id, actor_id) as conn:
        doc_ids = {str(r["id"]) for r in rows(
            conn, "SELECT id FROM documents WHERE case_id = :c AND id = ANY(:ids)",
            c=case_id, ids=[UUID(i) for i in input_ids])}
        media_ids = {str(r["id"]) for r in rows(
            conn, "SELECT id FROM media WHERE case_id = :c AND id = ANY(:ids)",
            c=case_id, ids=[UUID(i) for i in input_ids])}

    results: list[dict[str, Any]] = []
    media_ok_ids: list[str] = []
    errors = 0
    for source_id in input_ids:
        kind = "document" if source_id in doc_ids else "media" if source_id in media_ids else None
        if kind is None:
            results.append({"id": source_id, "status": "error", "error": "source_not_found"})
            errors += 1
            continue
        table = "documents" if kind == "document" else "media"
        mode_col = "ocr_mode" if kind == "document" else "asr_mode"
        mrow: dict | None = None
        do_process = False
        try:
            with tx(org_id, actor_id) as conn:
                mrow = one(conn, f"SELECT {mode_col} AS mode, sha256, storage_uri FROM {table} WHERE id = :i",
                           i=source_id)
            do_process = bool(mrow and mrow.get("mode"))
            with metrics.track_stage(f"file_ingest_{kind}", org_id):
                if do_process:
                    with tx(org_id, actor_id) as conn:
                        if kind == "document":
                            pipeline_result = process_document(conn, source_id, org_id, case_id, actor_id)
                        else:
                            # Solo ASR aquí; la diarización/visión va en un job aparte
                            # (media_diarize) para no sumar su pico de memoria al de Whisper.
                            pipeline_result = process_media(conn, source_id, org_id, case_id, actor_id,
                                                            do_diarization=False)
                    with tx(org_id, actor_id) as conn:
                        if kind == "document":
                            index_result = indexing.index_document(conn, org_id, case_id, source_id, actor_id)
                        else:
                            index_result = indexing.index_media(conn, org_id, case_id, source_id, actor_id)
                else:
                    pipeline_result = {"skipped": True, "reason": "sin_procesar"}
                    index_result = {"chunks": 0}
            results.append({"id": source_id, "kind": kind, "status": "ok", "processed": do_process,
                            "pipeline": pipeline_result, "chunks": index_result.get("chunks", 0)})
            if kind == "media" and do_process:
                media_ok_ids.append(source_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("file_ingest falló para %s (%s)", source_id, kind)
            errors += 1
            results.append({"id": source_id, "kind": kind, "status": "error", "error": str(exc)})
            try:
                with tx(org_id, actor_id) as conn:
                    one(conn, f"UPDATE {table} SET processing_status = 'FAILED' WHERE id = :i RETURNING id",
                        i=source_id)
            except Exception:
                log.exception("no se pudo marcar %s como FAILED", source_id)
        finally:
            # Documentos y medios NO procesados: sube el original a GCS y borra la copia
            # local. Los medios SÍ procesados suben su original en la etapa 2 (diarización),
            # DESPUÉS de asignar hablantes: así la subida (lenta con internet lento) no
            # retrasa la diarización y la copia local se reutiliza sin re-descargar.
            if not (kind == "media" and do_process):
                _store_original(org_id, actor_id, source_id, mrow)

    # 2b. Etapa 2 de los medios (diarización + identificación visual) en job/proceso
    #     aparte: así no se suma su pico de memoria (~2-3 GB) al de Whisper.
    if media_ok_ids:
        try:
            from app.workers.handlers.media_diarize import enqueue_media_diarization
            enqueue_media_diarization(org_id, case_id, actor_id, media_ok_ids)
        except Exception:  # noqa: BLE001
            log.exception("no se pudo encolar la diarización (caso %s)", case_id)

    # 3. Extracción legal (entidades/claims/facts) de lo ingerido. Su handler
    #    encadena el graph_build del caso al terminar, de modo que pgvector (ya
    #    reindexado arriba) y el grafo quedan al día automáticamente.
    ok_ids = [r["id"] for r in results if r.get("status") == "ok"]
    if ok_ids:
        enqueue_legal_extraction(org_id, case_id, actor_id, ok_ids)

    # 4. PARTES automáticas (OCR de documentos + hablantes ASR): crea las que falten y
    #    omite las que ya existen (dedup por nombre normalizado).
    if get_settings().AUTO_EXTRACT_PARTIES:
        try:
            from app.services import party_extraction
            with tx(org_id, actor_id) as conn:
                res = party_extraction.auto_extract_and_upsert(conn, org_id, case_id)
            if res.get("created"):
                log.info("partes automáticas: creadas=%s omitidas=%s (caso %s)",
                         len(res["created"]), res.get("skipped"), case_id)
        except Exception:  # noqa: BLE001
            log.exception("extracción automática de partes falló (caso %s)", case_id)

    return {"processed": len(results), "errors": errors, "files": results}


def _store_original(org_id: str, actor_id: str, source_id: str, mrow: dict | None,
                    keep_local: bool = False) -> None:
    """Sube el original a storage (GCS/S3) desde la copia local.

    La subida servidor->GCS puede ser lenta; se hace AQUÍ (en el worker, tras el OCR/ASR)
    para no bloquear la respuesta de la subida. Clave con internet lento.
    `keep_local=True` (medios procesados) conserva la copia local para la etapa de
    diarización, que la borra al terminar."""
    try:
        sha = mrow.get("sha256") if mrow else None
        uri = mrow.get("storage_uri") if mrow else None
        if not sha or not uri:
            return
        local = incoming_dir() / str(sha)
        if local.exists():
            storage().put_file(key_from_uri(str(uri)), local)
            if not keep_local:
                local.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        log.exception("no se pudo almacenar el original de %s", source_id)
