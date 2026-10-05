"""Backups programados (SSD §113). PostgreSQL daily via Celery beat."""
from __future__ import annotations

import logging

from app.core.db import one, rows, tx
from app.services import backup
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="backups.daily")
def daily_backups() -> int:
    """Ejecuta un backup diario por cada organización registrada."""
    with tx(None) as c:
        orgs = rows(c, "SELECT id FROM ops_list_organizations()")
    count = 0
    for org in orgs:
        org_id = str(org["id"])
        try:
            with tx(org_id) as c:
                b = one(c, """INSERT INTO backups (organization_id, backup_type, status)
                              VALUES (:o, 'scheduled', 'RUNNING') RETURNING id""", o=org_id)
                backup.backup_postgres(c, org_id, str(b["id"]))
            count += 1
        except Exception:
            log.exception("backup diario falló para org %s", org_id)
    return count
