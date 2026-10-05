"""Instancia Celery compartida: la API la usa para encolar y el worker para
ejecutar. Broker/backend salen de Settings (sin valores por defecto).
Con CELERY_TASK_ALWAYS_EAGER=true (tests/desarrollo sin broker) las tareas se
ejecutan de forma síncrona en el proceso que las encola."""
from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

_settings = get_settings()

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
