"""Máquinas de estado (SSD §22, §69, §70). Transiciones no listadas => INVALID_STATE_TRANSITION."""
CASE_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"UPLOADING", "INGESTING", "ARCHIVED"},
    "UPLOADING": {"INGESTING", "FAILED"},
    "INGESTING": {"PROCESSING", "FAILED"},
    "PROCESSING": {"PARTIALLY_READY", "READY_FOR_REVIEW", "FAILED"},
    "PARTIALLY_READY": {"PROCESSING", "READY_FOR_REVIEW", "FAILED"},
    "READY_FOR_REVIEW": {"REVIEWING", "PROCESSING"},
    "REVIEWING": {"READY", "READY_FOR_REVIEW", "PROCESSING"},
    "READY": {"PROCESSING", "REVIEWING", "ARCHIVED"},
    "FAILED": {"PROCESSING", "ARCHIVED"},
    "ARCHIVED": set(),
}

JOB_TRANSITIONS: dict[str, set[str]] = {
    "QUEUED": {"RUNNING", "CANCELLED"},
    # -> QUEUED: recuperación de jobs huérfanos (worker muerto) por el sweeper
    "RUNNING": {"SUCCEEDED", "FAILED", "RETRYING", "CANCELLED", "QUEUED"},
    "RETRYING": {"RUNNING", "FAILED", "CANCELLED", "QUEUED"},
    "FAILED": {"RETRYING"},
    "SUCCEEDED": set(),
    "CANCELLED": set(),
}

# SSD §77: errores transitorios => retry; deterministas => revisión manual
RETRYABLE_ERRORS = {"network_error", "model_timeout", "schema_error", "provider_unavailable"}
MANUAL_REVIEW_ERRORS = {"invalid_pdf", "ambiguous_identity", "corrupted_media", "unsupported_format"}

FACT_STATUSES = {"ALLEGED", "DISPUTED", "SUPPORTED", "CONTRADICTED", "JUDICIALLY_DETERMINED", "UNRESOLVED"}


def can_transition(table: dict[str, set[str]], current: str, target: str) -> bool:
    return target in table.get(current, set())


def retry_policy(error_code: str) -> str:
    if error_code in RETRYABLE_ERRORS:
        return "retry"
    return "manual_review"
