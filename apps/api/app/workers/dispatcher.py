"""Encolado de jobs desde la API. Un broker caído nunca rompe `POST /process`:
el job queda QUEUED y puede re-encolarse (idempotencia del executor). En modo
eager (CELERY_TASK_ALWAYS_EAGER=true, tests/desarrollo sin broker) la ejecución
es síncrona dentro del proceso de la API."""
from __future__ import annotations

import hashlib
import json
import logging
from uuid import UUID

from sqlalchemy.engine import Connection

from app.core.db import one

log = logging.getLogger(__name__)

# Los jobs de medios (ASR y diarización) van a su PROPIA cola. Así un worker dedicado
# (`worker-media`) los ejecuta y NUNCA quedan bloqueados detrás de la extracción legal
# (legal_extraction, graph_build), que es de red (LLM) y puede tardar muchísimo en la
# cola general. El resto de jobs van a la cola "default".
MEDIA_QUEUE = "media"
DEFAULT_QUEUE = "default"
_MEDIA_JOB_TYPES = {"media_asr", "media_diarize"}


def queue_for_job_type(job_type: str | None) -> str:
    return MEDIA_QUEUE if job_type in _MEDIA_JOB_TYPES else DEFAULT_QUEUE


def create_job(c: Connection, *, org_id: str, case_id: str, job_type: str, input_ids: list[str],
               actor_id: str, key_parts: list, pipeline_version: str, model_version: str) -> dict:
    """Crea un job idempotente dentro de una transacción abierta (SSD §24):
    misma clave => se reusa el job existente (ON CONFLICT DO NOTHING)."""
    key = hashlib.sha256(json.dumps(key_parts).encode()).hexdigest()
    j = one(c, """INSERT INTO jobs (organization_id, case_id, job_type, input_ids, idempotency_key,
                   pipeline_version, model_version, created_by)
                  VALUES (:o,:c,:t,CAST(:i AS uuid[]),:k,:pv,:mv,:u)
                  ON CONFLICT (organization_id, idempotency_key) DO NOTHING
                  RETURNING id, job_type, status""",
            o=org_id, c=case_id, t=job_type, i="{" + ",".join(input_ids) + "}", k=key,
            pv=pipeline_version, mv=model_version, u=actor_id)
    if j is None:
        j = one(c, "SELECT id, job_type, status FROM jobs WHERE organization_id = :o AND idempotency_key = :k",
                o=org_id, k=key)
        return {**j, "reused": True}
    return {**j, "reused": False}


def enqueue_if_pending(job: dict, org_id: str, actor_id: str) -> None:
    """Encola el job si es nuevo o si sigue QUEUED (misma regla que POST /process)."""
    if not job["reused"] or job["status"] == "QUEUED":
        enqueue_job(job["id"], org_id, actor_id, job.get("job_type"))


def enqueue_job(job_id: UUID | str, org_id: UUID | str, actor_id: UUID | str,
                job_type: str | None = None) -> None:
    from app.services import metrics
    from app.workers.executor import run_job, run_media

    metrics.track_job_queued(str(org_id))
    queue = queue_for_job_type(job_type)
    # Los medios usan la tarea con time_limit amplio (`jobs.run_media`).
    task = run_media if queue == MEDIA_QUEUE else run_job
    try:
        task.apply_async(args=[str(job_id), str(org_id), str(actor_id)], queue=queue)
    except Exception:  # broker inalcanzable: el job queda QUEUED, no se pierde
        log.exception("no se pudo encolar el job %s; queda QUEUED", job_id)
