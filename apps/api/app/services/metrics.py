"""Métricas de la aplicación (SSD §28). Instrumentación OpenTelemetry.

Las métricas se exponen en /metrics en formato Prometheus. Los logs JSON
nunca incluyen contenido jurídico (solo IDs, counts, duraciones).
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# Contadores de procesamiento
docs_processed = Counter("judicial_docs_processed_total", "Documentos procesados", ["org_id", "status"])
pages_processed = Counter("judicial_pages_processed_total", "Páginas OCR procesadas", ["org_id", "status"])
media_hours_processed = Counter("judicial_media_hours_processed_total", "Horas de media procesadas", ["org_id"])

# Latencias por etapa
stage_latency = Histogram(
    "judicial_stage_latency_seconds",
    "Latencia por etapa del pipeline",
    ["stage"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

# LLM
llm_tokens = Counter("judicial_llm_tokens_total", "Tokens LLM consumidos", ["org_id", "provider", "model", "task"])
llm_cost = Counter("judicial_llm_cost_total", "Costo LLM estimado (USD)", ["org_id", "provider", "model", "task"])

# Calidad
citation_accuracy = Gauge("judicial_citation_accuracy", "Precisión de citas en respuestas", ["org_id", "case_id"])
retrieval_hit_rate = Gauge("judicial_retrieval_hit_rate", "Tasa de acierto de retrieval", ["org_id", "case_id"])

# Backlog
jobs_queued = Gauge("judicial_jobs_queued", "Jobs en cola", ["org_id"])
jobs_failed = Counter("judicial_jobs_failed_total", "Jobs fallidos", ["org_id", "job_type", "error_code"])

# OCR/ASR
ocr_failures = Counter("judicial_ocr_failures_total", "Fallos de OCR", ["org_id", "provider"])
asr_failures = Counter("judicial_asr_failures_total", "Fallos de ASR", ["org_id", "provider"])

# Rate limiting
rate_limit_hits = Counter("judicial_rate_limit_hits_total", "Rate limit aplicado", ["org_id", "endpoint"])

# Perfil de intención por usuario (0052): estado del aprendizaje y su coste.
intent_profile_status = Gauge(
    "judicial_intent_profile_status",
    "Estado del perfil de intención por usuario (1 = estado actual)",
    ["org_id", "status"],
)
intent_profile_navegar_fp = Gauge(
    "judicial_intent_profile_navegar_fp",
    "Falsos positivos de navegar medidos en sombra",
    ["org_id", "kind"],
)
intent_profile_exemplars = Gauge(
    "judicial_intent_profile_exemplars",
    "Ejemplos aprendidos por los perfiles de la organización",
    ["org_id"],
)
intent_profile_degraded = Counter(
    "judicial_intent_profile_degraded_total",
    "Perfiles que dejaron de decidir por empeorar",
    ["org_id", "reason"],
)


@contextmanager
def track_stage(stage: str, org_id: str | None = None):
    """Context manager para medir latencia de una etapa."""
    t0 = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - t0
        stage_latency.labels(stage=stage).observe(elapsed)
        if org_id:
            log_stage_latency(stage, org_id, elapsed)


def log_stage_latency(stage: str, org_id: str, seconds: float):
    """Log JSON sin contenido jurídico."""
    import json
    import logging
    logging.getLogger("metrics").info(json.dumps({
        "metric": "stage_latency",
        "stage": stage,
        "org_id": org_id,
        "seconds": round(seconds, 3),
    }))


def track_llm_usage(org_id: str, provider: str, model: str, task: str, tokens_in: int, tokens_out: int, cost: float):
    """Registra uso de LLM."""
    llm_tokens.labels(org_id=org_id, provider=provider, model=model, task=task).inc(tokens_in + tokens_out)
    llm_cost.labels(org_id=org_id, provider=provider, model=model, task=task).inc(cost)


def track_job_queued(org_id: str):
    """Registra un job encolado."""
    jobs_queued.labels(org_id=org_id).inc()


def track_job_completed(org_id: str):
    """Registra un job completado."""
    jobs_queued.labels(org_id=org_id).dec()


def track_job_failed(org_id: str, job_type: str, error_code: str):
    """Registra un job fallido."""
    jobs_failed.labels(org_id=org_id, job_type=job_type, error_code=error_code).inc()


def track_ocr_failure(org_id: str, provider: str):
    """Registra un fallo de OCR."""
    ocr_failures.labels(org_id=org_id, provider=provider).inc()


def track_asr_failure(org_id: str, provider: str):
    """Registra un fallo de ASR."""
    asr_failures.labels(org_id=org_id, provider=provider).inc()


def track_rate_limit(org_id: str, endpoint: str):
    """Registra un hit de rate limit."""
    rate_limit_hits.labels(org_id=org_id, endpoint=endpoint).inc()


def update_citation_accuracy(org_id: str, case_id: str, accuracy: float):
    """Actualiza la precisión de citas."""
    citation_accuracy.labels(org_id=org_id, case_id=case_id).set(accuracy)


def update_retrieval_hit_rate(org_id: str, case_id: str, hit_rate: float):
    """Actualiza la tasa de acierto de retrieval."""
    retrieval_hit_rate.labels(org_id=org_id, case_id=case_id).set(hit_rate)


def track_intent_profile(org_id: str, *, status: str, reason: str, exemplars: int,
                         fp_profile: int, fp_rules: int):
    """Publica el estado y la salud del perfil de intención de una organización."""
    for value in ("off", "shadow", "active"):
        intent_profile_status.labels(org_id=org_id, status=value).set(1.0 if value == status else 0.0)
    intent_profile_exemplars.labels(org_id=org_id).set(float(exemplars))
    intent_profile_navegar_fp.labels(org_id=org_id, kind="profile").set(float(fp_profile))
    intent_profile_navegar_fp.labels(org_id=org_id, kind="rules").set(float(fp_rules))


def track_intent_profile_degraded(org_id: str, reason: str):
    """Un perfil que decidía dejó de hacerlo: es una alerta, no un detalle."""
    intent_profile_degraded.labels(org_id=org_id, reason=reason or "unknown").inc()


def metrics_endpoint() -> bytes:
    """Genera el endpoint /metrics en formato Prometheus."""
    return generate_latest()
