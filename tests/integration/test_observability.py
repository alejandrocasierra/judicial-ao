"""IT-OBS — observabilidad: métricas, alertas, backups."""
import pytest

pytestmark = pytest.mark.integration


def test_it_obs_01_metrics_endpoint_exists(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "judicial_" in r.text
    assert "judicial_docs_processed_total" in r.text
    assert "judicial_llm_tokens_total" in r.text


def test_it_obs_02_metrics_include_required_metrics(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    metrics_text = r.text
    required = [
        "judicial_docs_processed_total",
        "judicial_pages_processed_total",
        "judicial_media_hours_processed_total",
        "judicial_stage_latency_seconds",
        "judicial_llm_tokens_total",
        "judicial_llm_cost_total",
        "judicial_citation_accuracy",
        "judicial_retrieval_hit_rate",
        "judicial_jobs_queued",
        "judicial_jobs_failed_total",
        "judicial_ocr_failures_total",
        "judicial_asr_failures_total",
        "judicial_rate_limit_hits_total",
    ]
    for metric in required:
        assert metric in metrics_text, f"Métrica {metric} no encontrada"


def test_it_obs_03_alerts_endpoint_requires_auth(client):
    r = client.get("/v1/admin/alerts")
    assert r.status_code == 401


def test_it_obs_04_alerts_endpoint_returns_alerts(client, auth):
    r = client.get("/v1/admin/alerts", headers=auth("admin.alfa"))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_it_obs_05_backups_verify_rpo_requires_auth(client):
    r = client.get("/v1/admin/backups/verify-rpo")
    assert r.status_code == 401


def test_it_obs_06_backups_verify_rpo_returns_status(client, auth):
    r = client.get("/v1/admin/backups/verify-rpo", headers=auth("admin.alfa"))
    assert r.status_code == 200
    assert "rpo_met" in r.json()


def test_it_obs_07_backups_restore_test_requires_auth(client):
    r = client.post("/v1/admin/backups/restore-test")
    assert r.status_code == 401


def test_it_obs_08_backups_restore_test_returns_status(client, auth):
    r = client.post("/v1/admin/backups/restore-test", headers=auth("admin.alfa"))
    assert r.status_code == 200
    assert "status" in r.json()
