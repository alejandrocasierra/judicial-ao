"""Ejecutor de jobs (SSD §23, §77). Consume la máquina de estados de
`app/domain/states.py`; no la reinventa.

Flujo de `run_job`:
  1. Reclama el job bajo RLS con la organización correcta (SELECT ... FOR UPDATE).
     SUCCEEDED/CANCELLED/RUNNING => no hace nada (idempotencia: re-ejecutar un job
     terminado o reclamado por otro worker es un no-op).
  2. Ejecuta el handler registrado en `registry.py`.
  3. Éxito => SUCCEEDED + constancia en `model_runs`. Error => RETRYING con reintento
     Celery si `states.retry_policy` lo clasifica como transitorio; FAILED si no.

El worker usa el rol de aplicación (sin BYPASSRLS): el org llega en el payload de
la tarea (lo fija la API desde el principal autenticado) y `tx()` lo aplica, por lo
que el worker sólo ve filas de esa organización.

`run_job` usa acks_late=True: si el worker muere a mitad de la ejecución, el broker
re-entrega el mensaje (la idempotencia del executor hace segura la re-ejecución) y,
como red de seguridad, `reap_stale_jobs` devuelve a QUEUED los jobs que queden
huérfanos. time_limit acota un handler colgado: el job no puede quedar RUNNING
para siempre por un proceso vivo pero atascado.
"""
from __future__ import annotations

import json
import logging
import random
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.domain import states
from app.services import audit, metrics
from app.workers.celery_app import celery_app
from app.workers.registry import HANDLERS

log = logging.getLogger(__name__)

# Reintentos ante errores transitorios (SSD §77). No es configuración de entorno:
# es la política fija del ejecutor, igual que la ventana de 60 s de ratelimit.py.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 30
RETRY_JITTER_SECONDS = 15

UNKNOWN_JOB_TYPE = "unknown_job_type"
INTERNAL_ERROR = "internal_error"
STALE_REQUEUED = "stale_requeued"


class JobError(Exception):
    """Error clasificado por el handler. `states.retry_policy(error_code)` decide
    si es transitorio (reintento) o determinista (revisión manual, sin reintento)."""

    def __init__(self, error_code: str, detail: str = ""):
        super().__init__(detail or error_code)
        self.error_code = error_code


def retry_countdown(retries: int, jitter: float | None = None) -> float:
    """Backoff exponencial + jitter (evita una estampida de reintentos sincronizados).
    `jitter` inyectable para tests deterministas; None => uniforme real."""
    j = random.uniform(0, RETRY_JITTER_SECONDS) if jitter is None else jitter
    return RETRY_BACKOFF_SECONDS * (2 ** retries) + j


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def decide_action(status: str) -> str:
    """Política pura de arranque: qué hacer con un job según su estado actual."""
    if status in ("SUCCEEDED", "CANCELLED"):
        return "skip"  # terminal: idempotencia
    if status == "RUNNING":
        return "skip"  # ya reclamado por otro worker
    if status in ("QUEUED", "RETRYING", "FAILED"):
        return "run"
    raise JobError(INTERNAL_ERROR, f"estado de job desconocido: {status}")


def _set_status(c: Connection, job: dict[str, Any], target: str, error_code: str | None = None) -> dict[str, Any]:
    """Transición validada contra la máquina de estados (trg_jobs_touch fija updated_at).
    Devuelve una copia del job con el nuevo estado; el dict original no se muta."""
    if not states.can_transition(states.JOB_TRANSITIONS, job["status"], target):
        raise JobError(INTERNAL_ERROR, f"transición de job inválida: {job['status']} -> {target}")
    one(c, "UPDATE jobs SET status = :s, error_code = :e WHERE id = :i RETURNING id",
        s=target, e=error_code, i=str(job["id"]))
    return {**job, "status": target, "error_code": error_code}


def _claim(job_id: str, org_id: str) -> dict[str, Any] | None:
    """Reclama el job (QUEUED/RETRYING/FAILED -> RUNNING) o devuelve None si no procede."""
    with tx(org_id) as c:
        job = one(c, "SELECT id, organization_id, case_id, job_type, input_ids, status, attempts, "
                     "pipeline_version, model_version, idempotency_key, created_by FROM jobs WHERE id = :i FOR UPDATE",
                  i=job_id)
        if job is None:
            log.warning("job %s no visible para la organización (RLS) o inexistente", job_id)
            return None
        if decide_action(job["status"]) == "skip":
            return None
        if job["status"] == "FAILED":
            job = _set_status(c, job, "RETRYING")  # FAILED -> RUNNING no existe; se pasa por RETRYING
        if not states.can_transition(states.JOB_TRANSITIONS, job["status"], "RUNNING"):
            raise JobError(INTERNAL_ERROR, f"transición de job inválida: {job['status']} -> RUNNING")
        claimed = one(c, "UPDATE jobs SET status = 'RUNNING', attempts = attempts + 1, error_code = NULL "
                         "WHERE id = :i RETURNING attempts", i=job_id)
        return {**job, "status": "RUNNING", "error_code": None, "attempts": claimed["attempts"]}


def _succeed(job: dict[str, Any], result: dict[str, Any], actor_id: str) -> None:
    """Marca SUCCEEDED y deja constancia del resultado en model_runs (audit trail técnico).
    task = 'job:<tipo>' distingue estas filas de las ejecuciones reales de modelos
    (deuda registrada en BACKLOG: resultados de jobs a tabla/columna propia en fases 2-5)."""
    metrics.track_job_completed(str(job["organization_id"]))
    with tx(str(job["organization_id"])) as c:
        one(c, "INSERT INTO model_runs (organization_id, case_id, task, provider, model, pipeline_version, "
               "input_hash, output) VALUES (:o,:c,:t,:p,:m,:pv,:ih,CAST(:out AS jsonb)) RETURNING id",
            o=str(job["organization_id"]), c=str(job["case_id"]), t="job:" + job["job_type"], p="worker",
            m=job["model_version"] or "stub", pv=job["pipeline_version"], ih=job["idempotency_key"],
            out=json.dumps(result, default=str))
        _set_status(c, job, "SUCCEEDED")
        # El actor es quien pidió el procesamiento (SSD §85: ¿qué usuario?); nunca NULL.
        audit.record(c, org_id=str(job["organization_id"]), actor_id=actor_id, action="job.succeeded",
                     entity_type="job", entity_id=str(job["id"]),
                     after={"job_type": job["job_type"], "implemented": result.get("implemented")})


def _fail(job: dict[str, Any], target: str, error_code: str, actor_id: str) -> None:
    metrics.track_job_failed(str(job["organization_id"]), job["job_type"], error_code)
    with tx(str(job["organization_id"])) as c:
        _set_status(c, job, target, error_code)
        if target == "FAILED":
            audit.record(c, org_id=str(job["organization_id"]), actor_id=actor_id, action="job.failed",
                         entity_type="job", entity_id=str(job["id"]),
                         after={"job_type": job["job_type"], "error_code": error_code,
                                "policy": states.retry_policy(error_code)})


def _execute(task, job_id: str, org_id: str, actor_id: str) -> str:
    """Cuerpo común de las tareas `jobs.run` y `jobs.run_media` (misma lógica; sólo
    cambia el `time_limit` de la tarea Celery). `task` es el `self` de la tarea (para
    `retry`). Devuelve el estado final: SUCCEEDED | FAILED | SKIPPED.

    `actor_id` es el usuario que pidió el procesamiento: toda fila de auditoría lo lleva.
    acks_late=True: si el worker muere a mitad, el broker re-entrega el mensaje y la
    idempotencia del executor hace segura la re-ejecución."""
    if not (_is_uuid(job_id) and _is_uuid(org_id) and _is_uuid(actor_id)):
        log.error("job descartado: argumentos no UUID (job_id=%r)", job_id)
        return "FAILED"  # determinista: mensaje malformado, no se toca la BD
    job = _claim(job_id, org_id)
    if job is None:
        return "SKIPPED"
    handler = HANDLERS.get(job["job_type"])
    if handler is None:  # tipo desconocido: fallo determinista, nunca se reintenta
        _fail(job, "FAILED", UNKNOWN_JOB_TYPE, actor_id)
        return "FAILED"
    try:
        result = handler(job)
    except JobError as exc:
        if states.retry_policy(exc.error_code) == "retry" and task.request.retries < MAX_RETRIES:
            _fail(job, "RETRYING", exc.error_code, actor_id)
            raise task.retry(exc=exc, countdown=retry_countdown(task.request.retries)) from exc
        _fail(job, "FAILED", exc.error_code, actor_id)
        return "FAILED"
    except Exception:
        log.exception("job %s: error no clasificado del handler", job_id)
        _fail(job, "FAILED", INTERNAL_ERROR, actor_id)
        return "FAILED"
    _succeed(job, result, actor_id)
    return "SUCCEEDED"


@celery_app.task(bind=True, name="jobs.run", max_retries=MAX_RETRIES,
                 acks_late=True, time_limit=get_settings().JOB_TIME_LIMIT_SECONDS)
def run_job(self, job_id: str, org_id: str, actor_id: str) -> str:
    """Jobs generales (OCR, extracción legal, grafo, etc.). Límite: JOB_TIME_LIMIT_SECONDS."""
    return _execute(self, job_id, org_id, actor_id)


@celery_app.task(bind=True, name="jobs.run_media", max_retries=MAX_RETRIES,
                 acks_late=True, time_limit=get_settings().MEDIA_JOB_TIME_LIMIT_SECONDS)
def run_media(self, job_id: str, org_id: str, actor_id: str) -> str:
    """Jobs de MEDIOS (ASR/diarización): límite amplio (MEDIA_JOB_TIME_LIMIT_SECONDS),
    porque diarizar horas de audio con pyannote en CPU puede tardar mucho. Corren en la
    cola "media" (worker dedicado) y el sweeper NO los barre (migración 0037)."""
    return _execute(self, job_id, org_id, actor_id)


@celery_app.task(name="jobs.reap_stale")
def reap_stale_jobs() -> int:
    """Devuelve a QUEUED los jobs huérfanos en RUNNING/RETRYING (worker muerto antes
    de terminar: con acks_late el mensaje se re-entrega, pero si se pierde el job
    quedaría colgado para siempre). Usa el umbral `JOB_STALE_MINUTES`."""
    return reap_orphans(get_settings().JOB_STALE_MINUTES)


def reap_orphans(minutes: int, include_media: bool = False) -> int:
    """Devuelve a QUEUED y re-encola los jobs RUNNING/RETRYING más antiguos que `minutes`.

    La función `jobs_reap_stale` es SECURITY DEFINER (BYPASSRLS): barre todas las
    organizaciones; sólo toca metadatos del job. Se usa:
    - con `JOB_STALE_MINUTES` por el sweeper de beat;
    - con un umbral corto (p. ej. 1 min) AL ARRANCAR el worker, para recuperar los jobs
      que quedaron huérfanos si el worker murió/reinició (evita que queden en "0%" siempre).

    Auditoría: se audita `job.requeued` con `jobs.created_by`. Jobs sin created_by se
    recuperan (vuelven a QUEUED) pero NO se re-encolan (sin actor no se ejecutan)."""
    from app.workers.dispatcher import enqueue_job  # lazy: dispatcher importa executor perezosamente

    with tx(None) as c:  # sin org: la propia función SQL es la que bypasea RLS
        stale = rows(c, "SELECT id, organization_id, case_id, job_type, previous_status, created_by "
                        "FROM jobs_reap_stale(:m, :inc)", m=minutes, inc=include_media)
    for job in stale:
        log.warning("job %s (%s) huérfano en %s -> QUEUED", job["id"], job["job_type"], job["previous_status"])
        if job["created_by"]:
            with tx(str(job["organization_id"])) as c:
                audit.record(c, org_id=str(job["organization_id"]), actor_id=str(job["created_by"]),
                             action="job.requeued", entity_type="job", entity_id=str(job["id"]),
                             before={"status": job["previous_status"]},
                             after={"status": "QUEUED", "reason": STALE_REQUEUED})
            enqueue_job(job["id"], job["organization_id"], job["created_by"], job["job_type"])
    return len(stale)
