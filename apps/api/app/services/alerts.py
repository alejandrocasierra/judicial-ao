"""Alertas operacionales (SSD §113). Monitorea backlog, fallos y presupuesto."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.engine import Connection

from app.core.db import one, rows

log = logging.getLogger(__name__)

# Umbrales
BACKLOG_THRESHOLD = 100  # jobs en cola
OCR_FAILURE_RATE_THRESHOLD = 0.15  # 15%
ASR_FAILURE_RATE_THRESHOLD = 0.15  # 15%
BUDGET_WARNING_80 = 0.80
BUDGET_WARNING_90 = 0.90
BUDGET_WARNING_100 = 1.00


def check_queue_backlog(conn: Connection, org_id: str) -> list[dict]:
    """Alerta si hay demasiados jobs en cola."""
    count = one(conn, "SELECT count(*) AS n FROM jobs WHERE status = 'QUEUED' AND organization_id = :o", o=org_id)["n"]
    if count > BACKLOG_THRESHOLD:
        return [{
            "severity": "warning",
            "alert": "queue_backlog",
            "message": f"Backlog de {count} jobs en cola",
            "value": count,
            "threshold": BACKLOG_THRESHOLD,
        }]
    return []


def check_ocr_failure_rate(conn: Connection, org_id: str) -> list[dict]:
    """Alerta si la tasa de fallo de OCR supera el umbral."""
    stats = one(conn, """
        SELECT
          count(*) FILTER (WHERE status = 'FAILED') AS failed,
          count(*) AS total
        FROM jobs
        WHERE organization_id = :o AND job_type = 'document_ocr' AND created_at > now() - interval '24 hours'
    """, o=org_id)
    if stats["total"] == 0:
        return []
    rate = stats["failed"] / stats["total"]
    if rate > OCR_FAILURE_RATE_THRESHOLD:
        return [{
            "severity": "critical",
            "alert": "ocr_failure_rate",
            "message": f"Tasa de fallo OCR: {rate:.1%}",
            "value": rate,
            "threshold": OCR_FAILURE_RATE_THRESHOLD,
        }]
    return []


def check_asr_failure_rate(conn: Connection, org_id: str) -> list[dict]:
    """Alerta si la tasa de fallo de ASR supera el umbral."""
    stats = one(conn, """
        SELECT
          count(*) FILTER (WHERE status = 'FAILED') AS failed,
          count(*) AS total
        FROM jobs
        WHERE organization_id = :o AND job_type = 'media_asr' AND created_at > now() - interval '24 hours'
    """, o=org_id)
    if stats["total"] == 0:
        return []
    rate = stats["failed"] / stats["total"]
    if rate > ASR_FAILURE_RATE_THRESHOLD:
        return [{
            "severity": "critical",
            "alert": "asr_failure_rate",
            "message": f"Tasa de fallo ASR: {rate:.1%}",
            "value": rate,
            "threshold": ASR_FAILURE_RATE_THRESHOLD,
        }]
    return []


def check_budget_thresholds(conn: Connection, org_id: str) -> list[dict]:
    """Alerta si algún caso supera el 80%, 90% o 100% del presupuesto LLM."""
    cases = rows(conn, """
        SELECT id, case_number, spent_llm_tokens, max_llm_tokens
        FROM cases
        WHERE organization_id = :o AND max_llm_tokens > 0
    """, o=org_id)
    alerts = []
    for c in cases:
        usage = c["spent_llm_tokens"] / c["max_llm_tokens"]
        if usage >= BUDGET_WARNING_100:
            alerts.append({
                "severity": "critical",
                "alert": "budget_exceeded",
                "message": f"Caso {c['case_number']} superó el 100% del presupuesto LLM",
                "case_id": str(c["id"]),
                "value": usage,
                "threshold": BUDGET_WARNING_100,
            })
        elif usage >= BUDGET_WARNING_90:
            alerts.append({
                "severity": "warning",
                "alert": "budget_90_percent",
                "message": f"Caso {c['case_number']} superó el 90% del presupuesto LLM",
                "case_id": str(c["id"]),
                "value": usage,
                "threshold": BUDGET_WARNING_90,
            })
        elif usage >= BUDGET_WARNING_80:
            alerts.append({
                "severity": "info",
                "alert": "budget_80_percent",
                "message": f"Caso {c['case_number']} superó el 80% del presupuesto LLM",
                "case_id": str(c["id"]),
                "value": usage,
                "threshold": BUDGET_WARNING_80,
            })
    return alerts


def check_all(conn: Connection, org_id: str) -> list[dict]:
    """Ejecuta todas las verificaciones y retorna las alertas activas."""
    alerts = []
    alerts.extend(check_queue_backlog(conn, org_id))
    alerts.extend(check_ocr_failure_rate(conn, org_id))
    alerts.extend(check_asr_failure_rate(conn, org_id))
    alerts.extend(check_budget_thresholds(conn, org_id))
    return alerts


def log_alerts(alerts: list[dict], org_id: str):
    """Log JSON sin contenido jurídico."""
    import json
    for alert in alerts:
        log.warning(json.dumps({
            "metric": "alert",
            "org_id": org_id,
            "alert": alert["alert"],
            "severity": alert["severity"],
            "message": alert["message"],
            "value": alert.get("value"),
            "threshold": alert.get("threshold"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
