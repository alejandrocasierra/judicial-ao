"""IT-WRK — pipeline de jobs end-to-end: POST /process encola y el executor
(modo eager, CELERY_TASK_ALWAYS_EAGER=true en .env.test) lleva el job a SUCCEEDED
contra PostgreSQL real. Verifica idempotencia y fallo determinista."""
import uuid

import pytest

pytestmark = pytest.mark.integration


def _new_case_number() -> str:
    return "".join(str(uuid.uuid4().int)[:23]).ljust(23, "0")


def _case_with_doc(client, auth, pdf_bytes) -> dict:
    h = auth("abogada.alfa")
    case = client.post("/v1/cases", headers=h, json={
        "jurisdiction": "co", "case_number": _new_case_number(), "title": "Caso pipeline worker", "language": "es"}).json()
    r = client.post(f"/v1/cases/{case['id']}/documents", headers=h,
                    files={"file": ("demanda.pdf", pdf_bytes(), "application/pdf")})
    assert r.status_code == 201, r.text
    return case


def _process(client, headers, case_id, job_types) -> list[dict]:
    r = client.post(f"/v1/cases/{case_id}/process", headers=headers, json={"job_types": job_types})
    assert r.status_code == 202, r.text
    return r.json()["jobs"]


def _jobs(client, headers, case_id) -> list[dict]:
    return client.get(f"/v1/cases/{case_id}/processing", headers=headers).json()["jobs"]


def test_it_wrk_01_process_executes_jobs_to_succeeded(client, auth, pdf_bytes, owner_db):
    case = _case_with_doc(client, auth, pdf_bytes)
    h = auth("abogada.alfa")
    created = _process(client, h, case["id"], ["document_ocr", "embedding"])
    assert all(j["reused"] is False for j in created)
    jobs = _jobs(client, h, case["id"])
    assert {j["job_type"] for j in jobs} == {"document_ocr", "embedding"}
    assert all(j["status"] == "SUCCEEDED" and j["attempts"] == 1 for j in jobs)
    with owner_db.cursor() as cur:  # constancia: el stub de Fase 0 se registró en model_runs (task='job:*')
        cur.execute("SELECT count(*) FROM model_runs WHERE case_id = %s AND task IN ('job:document_ocr','job:embedding')", (case["id"],))
        assert cur.fetchone()[0] == 2
    owner_db.rollback()


def test_it_wrk_02_process_is_idempotent_and_does_not_rerun(client, auth, pdf_bytes):
    case = _case_with_doc(client, auth, pdf_bytes)
    h = auth("abogada.alfa")
    a = _process(client, h, case["id"], ["legal_extraction"])
    b = _process(client, h, case["id"], ["legal_extraction"])
    assert [j["id"] for j in a] == [j["id"] for j in b] and b[0]["reused"] is True
    job = _jobs(client, h, case["id"])[0]
    assert job["status"] == "SUCCEEDED" and job["attempts"] == 1  # no se re-ejecutó


def test_it_wrk_03_executor_rerun_of_succeeded_job_is_noop(client, auth, pdf_bytes):
    from app.workers.executor import run_job

    case = _case_with_doc(client, auth, pdf_bytes)
    h = auth("abogada.alfa")
    org_id = client.get("/v1/auth/me", headers=h).json()["organization_id"]
    job_id = _process(client, h, case["id"], ["indexing"])[0]["id"]
    assert run_job(job_id, org_id, str(uuid.uuid4())) == "SKIPPED"  # ya SUCCEEDED: idempotencia del executor
    job = _jobs(client, h, case["id"])[0]
    assert job["status"] == "SUCCEEDED" and job["attempts"] == 1


def test_it_wrk_04_unknown_job_type_fails_deterministically(client, auth, pdf_bytes, owner_db):
    from app.workers.executor import UNKNOWN_JOB_TYPE, run_job

    case = _case_with_doc(client, auth, pdf_bytes)
    h = auth("abogada.alfa")
    org_id = client.get("/v1/auth/me", headers=h).json()["organization_id"]
    with owner_db.cursor() as cur:  # un job de un tipo que el registry no conoce
        cur.execute(
            "INSERT INTO jobs (organization_id, case_id, job_type, idempotency_key, pipeline_version) "
            "VALUES (%s, %s, 'tipo_inexistente', %s, '1.0.0') RETURNING id",
            (org_id, case["id"], uuid.uuid4().hex))
        job_id = str(cur.fetchone()[0])
    owner_db.commit()
    assert run_job(job_id, org_id, str(uuid.uuid4())) == "FAILED"
    again = run_job(job_id, org_id, str(uuid.uuid4()))  # FAILED -> RETRYING -> RUNNING -> FAILED: sigue fallando, no se queda colgado
    assert again == "FAILED"
    job = next(j for j in _jobs(client, h, case["id"]) if j["id"] == job_id)
    assert job["status"] == "FAILED" and job["error_code"] == UNKNOWN_JOB_TYPE and job["attempts"] == 2


def test_it_wrk_05_sweeper_requeues_only_stale_jobs(client, auth, pdf_bytes, owner_db):
    """Sweeper: un job viejo en RUNNING vuelve a QUEUED, se audita con su actor y se
    re-encola (en eager se re-ejecuta hasta SUCCEEDED); uno reciente queda intacto;
    una segunda pasada no encuentra nada (idempotente)."""
    from app.workers.executor import reap_stale_jobs

    case = _case_with_doc(client, auth, pdf_bytes)
    h = auth("abogada.alfa")
    old, fresh = (j["id"] for j in _process(client, h, case["id"], ["document_ocr", "embedding"]))
    with owner_db.cursor() as cur:
        # trg_jobs_touch sobrescribe updated_at en cada UPDATE: se desactiva para simular antigüedad
        cur.execute("ALTER TABLE jobs DISABLE TRIGGER trg_jobs_touch")
        cur.execute("UPDATE jobs SET status='RUNNING', updated_at = now() - interval '3 hours' WHERE id = %s", (old,))
        cur.execute("UPDATE jobs SET status='RUNNING', updated_at = now() WHERE id = %s", (fresh,))
        cur.execute("ALTER TABLE jobs ENABLE TRIGGER trg_jobs_touch")
    owner_db.commit()
    assert reap_stale_jobs() == 1
    got = {j["id"]: j for j in _jobs(client, h, case["id"])}
    # viejo: el sweeper lo devolvió a QUEUED y lo re-encoló; en eager se re-ejecutó de inmediato
    assert got[old]["status"] == "SUCCEEDED" and got[old]["attempts"] == 2 and got[old]["error_code"] is None
    assert got[fresh]["status"] == "RUNNING"  # reciente: intacto
    assert reap_stale_jobs() == 0  # idempotente
    me = client.get("/v1/auth/me", headers=h).json()
    with owner_db.cursor() as cur:
        cur.execute("SELECT actor_id::text, organization_id::text FROM audit_logs WHERE action='job.requeued' AND entity_id=%s", (old,))
        row = cur.fetchone()
    owner_db.rollback()
    assert row == (me["id"], me["organization_id"])  # actor/org no nulos (test_sec_aud_04)


def test_it_wrk_06_concurrent_process_requests_do_not_collide(client, app, auth, pdf_bytes):
    """Race de idempotency_key: N requests concurrentes producen UN solo job y ningún 500
    (INSERT ... ON CONFLICT DO NOTHING + re-SELECT del ganador)."""
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    case = _case_with_doc(client, auth, pdf_bytes)
    h = auth("abogada.alfa")

    def _post():
        with TestClient(app) as c:
            r = c.post(f"/v1/cases/{case['id']}/process", headers=h, json={"job_types": ["document_ocr"]})
            return r.status_code, r.json()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: _post(), range(6)))
    assert all(code == 202 for code, _ in results)
    assert len({j["jobs"][0]["id"] for _, j in results}) == 1  # un solo job: los demás reusan
    jobs = _jobs(client, h, case["id"])
    assert len(jobs) == 1 and jobs[0]["status"] == "SUCCEEDED" and jobs[0]["attempts"] == 1


def test_it_wrk_07_executor_cannot_touch_other_orgs_jobs(client, auth, pdf_bytes, owner_db, org_ids):
    """RLS en el worker: un job de la org alfa es invisible para el org beta."""
    from app.workers.executor import run_job

    case = _case_with_doc(client, auth, pdf_bytes)  # org alfa
    h = auth("abogada.alfa")
    job_id = _process(client, h, case["id"], ["document_ocr"])[0]["id"]
    with owner_db.cursor() as cur:  # vuelve a QUEUED para que el intento cross-org sea significativo
        cur.execute("UPDATE jobs SET status='QUEUED', attempts=0 WHERE id=%s", (job_id,))
    owner_db.commit()
    assert run_job(job_id, org_ids["beta"], str(uuid.uuid4())) == "SKIPPED"
    with owner_db.cursor() as cur:
        cur.execute("SELECT status, attempts FROM jobs WHERE id=%s", (job_id,))
        row = cur.fetchone()
    owner_db.rollback()
    assert row == ("QUEUED", 0)  # intacto: el ejecutor no vio la fila
