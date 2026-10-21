"""Job nocturno del perfil de intención por usuario (migración 0052).

Sin intervención: deriva ejemplos de la conducta (feedback + reformulaciones),
mide en sombra y activa o degrada el perfil según `intent_profile.status`.
"""
from __future__ import annotations

import logging

from app.services import intent_learning
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="intent.rebuild_profiles")
def rebuild_profiles(days: int = 90) -> int:
    """Reconstruye el perfil de todos los usuarios de todas las organizaciones."""
    result = intent_learning.rebuild_all(days=int(days))
    for row in result["report"]:
        log.info(
            "perfil de intención user=%s status=%s reason=%s exemplars=%s judged=%s "
            "rules_acc=%s profile_acc=%s fp_navegar=%s/%s",
            row.get("user_id"), row.get("status"), row.get("reason"),
            row.get("exemplars"), row.get("judged"), row.get("rules_accuracy"),
            row.get("profile_accuracy"), row.get("navegar_fp_profile"),
            row.get("navegar_fp_rules"))
    if not result["users"]:
        log.info("perfil de intención: sin usuarios con conducta que aprender todavía")
    return int(result["users"])
