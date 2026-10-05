"""UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia.
Sin base de datos: `tx` se sustituye por una conexión falsa que simula la fila del job."""
import contextlib
import json
import uuid
from typing import get_args

import pytest
from celery.exceptions import Retry

from app.domain import states
from app.schemas import ProcessIn
from app.workers import executor
from app.workers.registry import HANDLERS, PENDING_PHASE

pytestmark = pytest.mark.unit


def _schema_job_types() -> set[str]:
    """Tipos de job que la API acepta en POST /process (fuente de verdad: ProcessIn)."""
    return set(get_args(get_args(ProcessIn.model_fields["job_types"].annotation)[0]))


# ---------------------------------------------------------------- registro
def test_ut_wrk_01_every_schema_job_type_has_handler():
    assert set(HANDLERS) == _schema_job_types() == set(PENDING_PHASE)


def test_ut_wrk_02_stub_result_is_structured():
    job = {"job_type": "document_ocr", "input_ids": [uuid.uuid4(), uuid.uuid4()]}
    # Handlers ya implementados no devuelven stub; el resto sí.
    implemented = {"document_ocr", "document_classification", "media_asr", "legal_extraction",
                   "embedding", "indexing", "graph_build", "file_ingest", "xlsx_ingest"}
    stub_types = _schema_job_types() - implemented
    for jt in stub_types:
        result = HANDLERS[jt]({**job, "job_type": jt})
        assert result["implemented"] is False
        assert result["job_type"] == jt and result["input_count"] == 2
        assert result["pending_phase"] == PENDING_PHASE[jt] and result["note"]


def test_ut_wrk_03_decide_action():
    assert executor.decide_action("SUCCEEDED") == "skip"
    assert executor.decide_action("CANCELLED") == "skip"
    assert executor.decide_action("RUNNING") == "skip"
    assert executor.decide_action("QUEUED") == "run"
    assert executor.decide_action("RETRYING") == "run"
    assert executor.decide_action("FAILED") == "run"
    with pytest.raises(executor.JobError):
        executor.decide_action("ESTADO_INVENTADO")


# ------------------------------------------------------- conexión falsa
class _Row:
    def __init__(self, mapping):
        self._mapping = mapping


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):  # db.rows() itera el resultado completo
        return iter(self._rows)


class FakeConn:
    """Simula la fila de `jobs` en memoria y registra escrituras (model_runs/auditoría)."""

    def __init__(self, job: dict | None):
        self.job = job
        self.model_runs: list[dict] = []
        self.audits: list[dict] = []

    def execute(self, clause, params=None):
        sql, params = str(clause), params or {}
        if "FOR UPDATE" in sql:
            return _Res([_Row(dict(self.job))] if self.job else [])
        if sql.startswith("UPDATE jobs SET status = 'RUNNING'"):
            assert states.can_transition(states.JOB_TRANSITIONS, self.job["status"], "RUNNING")
            self.job.update(status="RUNNING", error_code=None, attempts=self.job["attempts"] + 1)
            return _Res([_Row({"attempts": self.job["attempts"]})])
        if sql.startswith("UPDATE jobs SET status = :s"):
            assert states.can_transition(states.JOB_TRANSITIONS, self.job["status"], params["s"])
            self.job.update(status=params["s"], error_code=params["e"])
            return _Res([_Row({"id": self.job["id"]})])
        if "INSERT INTO model_runs" in sql:
            self.model_runs.append(params)
            return _Res([_Row({"id": 1})])
        if "INSERT INTO audit_logs" in sql:
            self.audits.append(params)
            return _Res([])
        raise AssertionError(f"SQL inesperado en la prueba: {sql}")


def _job(**over):
    base = {"id": str(uuid.uuid4()), "organization_id": str(uuid.uuid4()), "case_id": str(uuid.uuid4()),
            "job_type": "document_ocr", "input_ids": [str(uuid.uuid4())], "status": "QUEUED", "attempts": 0,
            "pipeline_version": "1.0.0", "model_version": "modelo-x", "idempotency_key": "ab" * 32}
    return {**base, **over}


def _stub_handler(job_type: str):
    def handler(job: dict) -> dict:
        return {
            "implemented": False,
            "pending_phase": PENDING_PHASE[job_type],
            "job_type": job_type,
            "input_count": len(job.get("input_ids", [])),
            "note": "stub de test para el executor",
        }
    return handler


def _patched(monkeypatch, conn: FakeConn):
    @contextlib.contextmanager
    def _fake_tx(org_id):
        assert conn.job is None or org_id == conn.job["organization_id"]  # RLS: el org del payload es el del job
        yield conn

    monkeypatch.setattr(executor, "tx", _fake_tx)
    # Los tests unitarios del executor usan un handler stub, no el OCR real.
    monkeypatch.setitem(executor.HANDLERS, "document_ocr", _stub_handler("document_ocr"))


# ---------------------------------------------------------------- executor
ACTOR = str(uuid.uuid4())  # quien pidió el procesamiento; la auditoría lo exige no nulo


def test_ut_wrk_04_happy_path_marks_succeeded_and_records(monkeypatch):
    conn = FakeConn(_job())
    _patched(monkeypatch, conn)
    assert executor.run_job(conn.job["id"], conn.job["organization_id"], ACTOR) == "SUCCEEDED"
    assert conn.job["status"] == "SUCCEEDED" and conn.job["attempts"] == 1
    assert len(conn.model_runs) == 1 and '"implemented": false' in conn.model_runs[0]["out"]
    assert conn.model_runs[0]["ih"] == conn.job["idempotency_key"]
    assert any(a["act"] == "job.succeeded" for a in conn.audits)


@pytest.mark.parametrize("status", ["SUCCEEDED", "CANCELLED", "RUNNING"])
def test_ut_wrk_05_terminal_or_claimed_job_is_not_rerun(monkeypatch, status):
    conn = FakeConn(_job(status=status, attempts=3))
    _patched(monkeypatch, conn)
    assert executor.run_job(conn.job["id"], conn.job["organization_id"], ACTOR) == "SKIPPED"
    assert conn.job["status"] == status and conn.job["attempts"] == 3  # intacto
    assert conn.model_runs == [] and conn.audits == []


def test_ut_wrk_06_invisible_or_missing_job_is_skipped(monkeypatch):
    conn = FakeConn(None)  # RLS: sin org correcto el job no existe para el worker
    _patched(monkeypatch, conn)
    assert executor.run_job(str(uuid.uuid4()), str(uuid.uuid4()), ACTOR) == "SKIPPED"


def test_ut_wrk_07_unknown_job_type_fails_without_retry(monkeypatch):
    conn = FakeConn(_job(job_type="tipo_inexistente"))
    _patched(monkeypatch, conn)
    assert executor.run_job(conn.job["id"], conn.job["organization_id"], ACTOR) == "FAILED"
    assert conn.job["status"] == "FAILED" and conn.job["error_code"] == executor.UNKNOWN_JOB_TYPE
    assert states.retry_policy(conn.job["error_code"]) == "manual_review"  # determinista: no retry


def test_ut_wrk_08_retryable_error_marks_retrying_and_reraises(monkeypatch):
    conn = FakeConn(_job())
    _patched(monkeypatch, conn)

    def _boom(job):
        raise executor.JobError("model_timeout")

    monkeypatch.setitem(executor.HANDLERS, "document_ocr", _boom)
    with pytest.raises((Retry, executor.JobError)):  # self.retry(exc=...) relanza la excepción original
        executor.run_job(conn.job["id"], conn.job["organization_id"], ACTOR)
    assert conn.job["status"] == "RETRYING" and conn.job["error_code"] == "model_timeout"


def test_ut_wrk_09_manual_review_error_fails_without_retry(monkeypatch):
    conn = FakeConn(_job())
    _patched(monkeypatch, conn)

    def _boom(job):
        raise executor.JobError("invalid_pdf")

    monkeypatch.setitem(executor.HANDLERS, "document_ocr", _boom)
    assert executor.run_job(conn.job["id"], conn.job["organization_id"], ACTOR) == "FAILED"
    assert conn.job["status"] == "FAILED" and conn.job["error_code"] == "invalid_pdf"
    assert any(a["act"] == "job.failed" for a in conn.audits)


def test_ut_wrk_10_unclassified_error_fails_as_internal(monkeypatch):
    conn = FakeConn(_job())
    _patched(monkeypatch, conn)

    def _boom(job):
        raise ValueError("algo inesperado")

    monkeypatch.setitem(executor.HANDLERS, "document_ocr", _boom)
    assert executor.run_job(conn.job["id"], conn.job["organization_id"], ACTOR) == "FAILED"
    assert conn.job["error_code"] == executor.INTERNAL_ERROR


def test_ut_wrk_11_failed_job_is_claimed_via_retrying(monkeypatch):
    """FAILED -> RUNNING no existe en la máquina de estados: el claim pasa por RETRYING."""
    conn = FakeConn(_job(status="FAILED", error_code="model_timeout", attempts=1))
    _patched(monkeypatch, conn)
    assert executor.run_job(conn.job["id"], conn.job["organization_id"], ACTOR) == "SUCCEEDED"
    assert conn.job["attempts"] == 2 and conn.job["status"] == "SUCCEEDED"


# ---------------------------------------------------------------- sweeper
class SweepConn:
    """Conexión falsa para el sweeper: responde la consulta jobs_reap_stale y registra auditoría."""

    def __init__(self, stale: list[dict]):
        self.stale = stale
        self.audits: list[dict] = []
        self.sweep_params: dict | None = None

    def execute(self, clause, params=None):
        sql, params = str(clause), params or {}
        if "jobs_reap_stale" in sql:
            self.sweep_params = params
            return _Res([_Row(dict(j)) for j in self.stale])
        if "INSERT INTO audit_logs" in sql:
            self.audits.append(params)
            return _Res([])
        raise AssertionError(f"SQL inesperado en la prueba: {sql}")


def _stale_job(**over):
    base = {"id": str(uuid.uuid4()), "organization_id": str(uuid.uuid4()), "case_id": str(uuid.uuid4()),
            "job_type": "document_ocr", "previous_status": "RUNNING", "created_by": ACTOR}
    return {**base, **over}


def _patch_sweep_tx(monkeypatch, conn: SweepConn, calls: list):
    @contextlib.contextmanager
    def _fake_tx(org_id):
        calls.append(org_id)
        yield conn

    monkeypatch.setattr(executor, "tx", _fake_tx)


def _patch_enqueue(monkeypatch) -> list[tuple]:
    """Sustituye dispatcher.enqueue_job por un stub que registra las llamadas."""
    from app.workers import dispatcher

    enqueued: list[tuple] = []
    monkeypatch.setattr(dispatcher, "enqueue_job", lambda *a: enqueued.append(a))
    return enqueued


def test_ut_wrk_12_sweeper_requeues_stale_jobs_and_audits_with_actor(monkeypatch):
    stale = [_stale_job(), _stale_job(previous_status="RETRYING")]
    conn, calls = SweepConn(stale), []
    _patch_sweep_tx(monkeypatch, conn, calls)
    enqueued = _patch_enqueue(monkeypatch)
    assert executor.reap_stale_jobs() == 2
    assert calls[0] is None  # el barrido es cross-org: la función SQL (SECURITY DEFINER) bypasea RLS
    from app.core.config import get_settings
    assert conn.sweep_params["m"] == get_settings().JOB_STALE_MINUTES
    assert sorted(calls[1:]) == sorted(j["organization_id"] for j in stale)
    assert len(conn.audits) == 2 and all(a["act"] == "job.requeued" for a in conn.audits)
    assert all(a["a"] == ACTOR for a in conn.audits)  # actor que originó el job, nunca NULL
    assert all(json.loads(a["b"])["status"] in ("RUNNING", "RETRYING") for a in conn.audits)
    # cada job recuperado se re-encola con su org y su actor (el claim colapsa doble encolado)
    assert sorted(enqueued) == sorted((j["id"], j["organization_id"], j["created_by"]) for j in stale)


def test_ut_wrk_13_sweeper_is_idempotent_when_nothing_stale(monkeypatch):
    conn, calls = SweepConn([]), []
    _patch_sweep_tx(monkeypatch, conn, calls)
    enqueued = _patch_enqueue(monkeypatch)
    assert executor.reap_stale_jobs() == 0
    assert calls == [None] and conn.audits == [] and enqueued == []  # sin recuperaciones: ni auditoría ni encolado


def test_ut_wrk_14_sweeper_without_known_actor_only_logs(monkeypatch, caplog):
    """Jobs antiguos sin created_by: se recuperan pero no se auditan con actor NULL
    (test_sec_aud_04) ni se re-encolan (sin actor confiable no se ejecutan)."""
    conn, calls = SweepConn([_stale_job(created_by=None)]), []
    _patch_sweep_tx(monkeypatch, conn, calls)
    enqueued = _patch_enqueue(monkeypatch)
    with caplog.at_level("WARNING", logger="app.workers.executor"):
        assert executor.reap_stale_jobs() == 1
    assert conn.audits == [] and enqueued == [] and "huérfano" in caplog.text


# ------------------------------------------------------- robustez del executor
def test_ut_wrk_15_retry_countdown_is_exponential_with_jitter():
    assert executor.retry_countdown(0, jitter=0.0) == executor.RETRY_BACKOFF_SECONDS
    assert executor.retry_countdown(2, jitter=1.5) == executor.RETRY_BACKOFF_SECONDS * 4 + 1.5
    for _ in range(50):  # jitter real acotado: [base, base + RETRY_JITTER_SECONDS]
        c = executor.retry_countdown(1)
        assert executor.RETRY_BACKOFF_SECONDS * 2 <= c <= executor.RETRY_BACKOFF_SECONDS * 2 + executor.RETRY_JITTER_SECONDS


def test_ut_wrk_16_non_uuid_arguments_fail_without_touching_db(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no debe tocar la BD con argumentos malformados")

    monkeypatch.setattr(executor, "tx", _boom)
    assert executor.run_job("no-es-uuid", str(uuid.uuid4()), ACTOR) == "FAILED"
    assert executor.run_job(str(uuid.uuid4()), "org-mala", ACTOR) == "FAILED"
    assert executor.run_job(str(uuid.uuid4()), str(uuid.uuid4()), "actor-malo") == "FAILED"


def test_ut_wrk_17_dispatcher_survives_broker_outage(monkeypatch):
    """Broker caído: enqueue_job no lanza; el job queda QUEUED y puede re-encolarse."""
    from kombu.exceptions import OperationalError

    from app.workers import dispatcher

    def _boom(*a, **k):
        raise OperationalError("broker caído")

    monkeypatch.setattr(executor.run_job, "delay", _boom)
    dispatcher.enqueue_job(str(uuid.uuid4()), str(uuid.uuid4()), ACTOR)  # no lanza


def test_ut_wrk_18_graph_refresh_correction_uses_distinct_key(monkeypatch):
    """Una corrección humana no queda suprimida por un graph_build de ingesta reciente:
    usa una clave de idempotencia distinta."""
    from app.workers.handlers import file_ingest

    captured: list[dict] = []

    def _fake_create_job(_conn, **kw):
        captured.append(kw)
        return {"id": str(uuid.uuid4()), "job_type": "graph_build", "status": "QUEUED", "reused": False}

    @contextlib.contextmanager
    def _fake_tx(_org, _actor=None):
        yield object()

    monkeypatch.setattr(file_ingest, "create_job", _fake_create_job)
    monkeypatch.setattr(file_ingest, "enqueue_if_pending", lambda *a: None)
    monkeypatch.setattr(file_ingest, "tx", _fake_tx)

    org, case, actor = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    file_ingest.enqueue_graph_refresh(org, case, actor)
    file_ingest.enqueue_graph_refresh(org, case, actor, correction=True)

    assert captured[0]["key_parts"] != captured[1]["key_parts"]
    assert any("correction" in str(p) for p in captured[1]["key_parts"])
