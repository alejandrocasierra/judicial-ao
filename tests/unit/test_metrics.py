"""UT-MET — métricas y alertas."""
from app.services import alerts, metrics

import pytest

pytestmark = pytest.mark.unit


def test_ut_met_01_metrics_endpoint_has_content():
    data = metrics.metrics_endpoint()
    assert b"judicial_" in data


def test_ut_met_02_track_stage_records_latency():
    with metrics.track_stage("unit_test_stage"):
        pass
    text = metrics.metrics_endpoint().decode()
    assert "judicial_stage_latency_seconds" in text


def test_ut_met_03_track_llm_usage():
    metrics.track_llm_usage("org1", "fake", "model-x", "answer_question", 10, 20, 0.001)
    text = metrics.metrics_endpoint().decode()
    assert "judicial_llm_tokens_total" in text


def test_ut_met_04_track_job_counters():
    metrics.track_job_queued("org1")
    metrics.track_job_completed("org1")
    metrics.track_job_failed("org1", "document_ocr", "internal_error")
    text = metrics.metrics_endpoint().decode()
    assert "judicial_jobs_queued" in text
    assert "judicial_jobs_failed_total" in text


def test_ut_met_05_quality_gauges():
    metrics.update_citation_accuracy("org1", "case1", 0.9)
    metrics.update_retrieval_hit_rate("org1", "case1", 0.5)
    text = metrics.metrics_endpoint().decode()
    assert "judicial_citation_accuracy" in text
    assert "judicial_retrieval_hit_rate" in text


def test_ut_met_06_alerts_log_does_not_raise(caplog):
    alerts.log_alerts([{"alert": "queue_backlog", "severity": "warning", "message": "x",
                        "value": 1, "threshold": 2}], "org1")
