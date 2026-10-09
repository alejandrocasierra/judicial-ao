"""Instancia Celery compartida: la API la usa para encolar y el worker para
ejecutar. Broker/backend salen de Settings (sin valores por defecto).
Con CELERY_TASK_ALWAYS_EAGER=true (tests/desarrollo sin broker) las tareas se
ejecutan de forma síncrona en el proceso que las encola."""
from __future__ import annotations

import logging

from celery import Celery
from celery.signals import worker_ready

from app.core.config import get_settings

_settings = get_settings()
log = logging.getLogger(__name__)

celery_app = Celery(
    "judicial",
    broker=_settings.CELERY_BROKER_URL,
    backend=_settings.CELERY_RESULT_BACKEND,
    include=["app.workers.executor", "app.workers.backups"],
)
celery_app.conf.update(
    task_always_eager=_settings.CELERY_TASK_ALWAYS_EAGER,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    # Cola por defecto = "default" (el worker general la consume). Los jobs de medios
    # (ASR/diarización) se publican aparte en la cola "media" (ver dispatcher) para que
    # un worker dedicado los corra y no queden detrás de la extracción legal (LLM) en
    # la cola general. Ver `queue_for_job_type` en app/workers/dispatcher.py.
    task_default_queue="default",
    task_create_missing_queues=True,
    # Prioridades (Redis): permite que la diarización (prioridad 9) salte por delante de
    # las ingestas en cola (prioridad 5) y así cada video se procesa completo, de a uno.
    task_queue_max_priority=10,
    task_default_priority=5,
    broker_transport_options={
        "priority_steps": list(range(10)),
        "queue_order_strategy": "priority",
        "visibility_timeout": max(3600, _settings.MEDIA_JOB_TIME_LIMIT_SECONDS + 600),
    },
    beat_schedule={
        # Sweeper de jobs huérfanos: cada media vida del umbral (con JOB_STALE_MINUTES=120,
        # cada 60 min). Requiere `celery -A app.workers.celery_app beat` (o --beat en el worker).
        "reap-stale-jobs": {
            "task": "jobs.reap_stale",
            "schedule": max(60.0, _settings.JOB_STALE_MINUTES * 30.0),
        },
        # Backup PostgreSQL diario a las 02:00 (SSD §113; RPO ≤ 1h con WAL).
        "daily-backups": {
            "task": "backups.daily",
            "schedule": 24 * 60 * 60.0,
        },
    },
)


@worker_ready.connect
def _reap_orphans_on_startup(**_kwargs) -> None:
    """Al arrancar el worker reencola los jobs RUNNING/RETRYING huérfanos de un reinicio
    previo (p. ej. el worker murió por OOM). Evita que queden "Procesando 0%" para siempre."""
    try:
        from app.workers.executor import reap_orphans
        # include_media=True: al arrancar, un job de medios RUNNING SIEMPRE es huérfano de un
        # reinicio (el mensaje redeliverado se saltaría por estar RUNNING). El sweeper
        # periódico, en cambio, NO barre medios en ejecución (umbral largo).
        n = reap_orphans(int(_settings.STARTUP_REAP_MINUTES), include_media=True)
        if n:
            log.warning("startup reap: %s job(s) huérfano(s) reencolado(s)", n)
    except Exception:  # noqa: BLE001 — nunca debe impedir que el worker arranque
        log.exception("no se pudieron reencolar jobs huérfanos al arrancar")
