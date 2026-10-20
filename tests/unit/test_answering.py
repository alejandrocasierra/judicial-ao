"""UT-ANS — recuperación híbrida FTS + vector + RRF, embudo y abstención blanda."""
import uuid
from typing import Any

import pytest

from app.services import answering as ans
from app.services import intent as intent_mod
from app.services import retrieval_plan
from app.services.retrieval_plan import RetrievalPlan

pytestmark = pytest.mark.unit

CASE = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """La caché es ortogonal al embudo: las pruebas la aíslan."""
    monkeypatch.setattr(ans, "_cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(ans, "_cache_set", lambda *_a, **_k: None)


def _chunk(**overrides) -> dict[str, Any]:
    base = {"id": uuid.uuid4(), "chunk_type": "page", "document_id": uuid.uuid4(),
            "page_number": 5, "media_id": None, "start_ms": None, "end_ms": None,
            "text": "El comprobante de pago", "metadata": {"folio": "12"},
            "filename": "doc.pdf", "score": 0.9}
    base.update(overrides)
    return base


class _Rows:
    """Stub de `rows()` que distingue las consultas del embudo."""

    def __init__(self, data: list[dict], present: str = "pago"):
        self.data = data
        self.present = present
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, _c, sql: str, **params):
        self.calls.append((sql, params))
        if "unnest(CAST(:terms AS text[]))" in sql:
            return [{"term": self.present}]
        if "string_agg(quote_literal" in sql:
            return [{"q": "pago | comprob"}]
        return list(self.data)

    def find(self, needle: str) -> list[dict]:
        return [params for sql, params in self.calls if needle in sql]


class _FakeProvider:
    name = "fake"
    dimensions = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class _FakeConn:
    def __init__(self, has_chunks: bool = True):
        self.has_chunks = has_chunks

    def execute(self, clause, params=None):
        return _FakeResult(1 if self.has_chunks else None)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


def _install(monkeypatch, rows_stub: _Rows) -> None:
    monkeypatch.setattr(ans, "rows", rows_stub)
    monkeypatch.setattr(ans, "get_embedding_provider", lambda: _FakeProvider())


def _plan(**overrides) -> RetrievalPlan:
    decision = intent_mod.classify("¿Qué evidencia hay sobre pagos?")
    plan = retrieval_plan.build("¿Qué evidencia hay sobre pagos?", decision)
    if not overrides:
        return plan
    from dataclasses import replace
    return replace(plan, **overrides)


def test_ut_ans_01_rrf_fuses_disjoint_lists():
    a = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    b = [{"id": "c"}, {"id": "d"}]
    fused = ans._rrf_fuse([a, b], k=10, rrf_k=60)
    ids = [it["id"] for it in fused]
    # c aparece en ambas listas -> debe ganar.
    assert ids[0] == "c"
    assert set(ids) == {"a", "b", "c", "d"}


def test_ut_ans_02_retrieve_uses_chunks(monkeypatch):
    chunks = [_chunk()]
    _install(monkeypatch, _Rows(chunks))
    monkeypatch.setattr(ans.abstention, "should_abstain", lambda _c, _case, _q, _min=None: False)
    items = ans.retrieve(_FakeConn(has_chunks=True), CASE, "pruebas del pago")
    assert len(items) == 1
    assert items[0]["source_type"] == "document_page"
    assert items[0]["folio"] == "12"
    assert items[0]["handle"] == "E1"


def test_ut_ans_03_zero_coverage_returns_empty(monkeypatch):
    rows = _Rows([_chunk()])
    _install(monkeypatch, rows)
    monkeypatch.setattr(ans.abstention, "term_coverage", lambda *_a, **_k: 0.0)
    items = ans.retrieve(_FakeConn(), CASE, "¿Qué pasó con los criptoactivos?")
    assert items == []


def test_ut_ans_04_low_coverage_does_not_abstain(monkeypatch):
    rows = _Rows([_chunk()])
    _install(monkeypatch, rows)
    monkeypatch.setattr(ans.abstention, "term_coverage", lambda *_a, **_k: 0.2)
    items = ans.retrieve(_FakeConn(), CASE, "pruebas del pago")
    assert len(items) == 1


def test_ut_ans_05_uses_candidate_k_not_final_k(monkeypatch):
    rows = _Rows([_chunk()])
    _install(monkeypatch, rows)
    plan = _plan()
    ans.retrieve(_FakeConn(), CASE, "pruebas del pago", plan=plan, intent=intent_mod.Intent.LOOKUP)
    fts_params = rows.find("ts_rank_cd")
    assert fts_params and fts_params[0]["k"] == plan.candidate_k
    vector_params = rows.find("<=>")
    assert vector_params and vector_params[0]["k"] == plan.candidate_k


def test_ut_ans_07_embedding_model_mismatch_falls_back(monkeypatch):
    class _Mismatch(_Rows):
        def __call__(self, _c, sql: str, **params):
            self.calls.append((sql, params))
            if "embedding_model = :em" in sql:
                return []  # chunks históricos con otro modelo/placeholder
            if "embedding_version = :ev" in sql:
                return []
            return list(self.data)

    rows = _Mismatch([_chunk()])
    _install(monkeypatch, rows)
    monkeypatch.setattr(ans.get_settings(), "RETRIEVAL_ALLOW_EMBEDDING_FALLBACK", True, raising=False)
    monkeypatch.setattr(ans, "_supplement_unindexed", lambda _c, _case, _q, items, _plan: items)
    plan = _plan(embedding_model="gemini-embedding-001")
    items = ans.retrieve(_FakeConn(), CASE, "pruebas del pago", plan=plan,
                         intent=intent_mod.Intent.LOOKUP)
    assert len(items) == 1
    vector_sql = [sql for sql, _ in rows.calls if "<=>" in sql]
    assert any("embedding_model = :em" not in sql for sql in vector_sql)


def test_ut_ans_06_filters_by_embedding_model(monkeypatch):
    rows = _Rows([_chunk()])
    _install(monkeypatch, rows)
    plan = _plan(embedding_model="fake-embed")
    ans.retrieve(_FakeConn(), CASE, "pruebas del pago", plan=plan, intent=intent_mod.Intent.LOOKUP)
    vector_sql = [sql for sql, _ in rows.calls if "<=>" in sql]
    assert any("embedding_model = :em" in sql for sql in vector_sql)
    em_params = [p for p in rows.find("<=>") if "em" in p]
    assert em_params and em_params[0]["em"] == plan.embedding_model


def test_ut_ans_08_strict_version_falls_back_when_empty(monkeypatch):
    chunk = _chunk()

    class _VersionAwareRows(_Rows):
        def __call__(self, _c, sql: str, **params):
            self.calls.append((sql, params))
            if "unnest(CAST(:terms AS text[]))" in sql:
                return [{"term": "pago"}]
            if "string_agg(quote_literal" in sql:
                return [{"q": "pago"}]
            if "<=>" in sql and "embedding_version = :ev" in sql:
                return []
            return [chunk]

    rows = _VersionAwareRows([])
    _install(monkeypatch, rows)
    plan = _plan(strict_embedding_version=True, embedding_version="v-test")
    items = ans.retrieve(_FakeConn(), CASE, "pruebas del pago", plan=plan,
                          intent=intent_mod.Intent.LOOKUP)
    assert items
    vector_calls = [sql for sql, _ in rows.calls if "<=>" in sql]
    assert any("embedding_version = :ev" in sql for sql in vector_calls)
    assert any("embedding_version = :ev" not in sql for sql in vector_calls)


def test_ut_ans_08_plan_does_not_call_locate(monkeypatch):
    from app.services.case_tools import read as read_tools

    def _boom(*_a, **_k):
        raise AssertionError("retrieve no debe llamar locate")

    monkeypatch.setattr(read_tools, "locate", _boom)
    _install(monkeypatch, _Rows([_chunk()]))
    items = ans.retrieve(_FakeConn(), CASE, "¿Qué evidencia hay sobre pagos?",
                         plan=_plan(), intent=intent_mod.Intent.LOOKUP)
    assert items


def test_ut_ans_09_start_mmss_for_transcript(monkeypatch):
    segment = _chunk(document_id=None, page_number=None, media_id=uuid.uuid4(),
                     start_ms=2426000, end_ms=2430000, text="el pago se discute",
                     metadata={"segment_id": "s1", "speaker": "Alba"}, filename="audiencia.mp4")
    _install(monkeypatch, _Rows([segment]))
    items = ans.retrieve(_FakeConn(), CASE, "pruebas del pago", plan=_plan(),
                         intent=intent_mod.Intent.LOOKUP)
    assert items[0]["start_mmss"] == "40:26"
    assert items[0]["speaker"] == "Alba"
    assert items[0]["score"] == 0.9


def test_ut_ans_10_handles_are_sequential(monkeypatch):
    chunks = [_chunk(page_number=1, document_id=uuid.uuid4(), filename="a.pdf"),
              _chunk(page_number=2, document_id=uuid.uuid4(), filename="b.pdf")]
    _install(monkeypatch, _Rows(chunks))
    items = ans.retrieve(_FakeConn(), CASE, "pruebas del pago", plan=_plan(),
                         intent=intent_mod.Intent.LOOKUP)
    assert [it["handle"] for it in items] == [f"E{i}" for i in range(1, len(items) + 1)]


def test_ut_ans_11_legacy_uses_plan_final_k(monkeypatch):
    rows = _Rows([])
    _install(monkeypatch, rows)
    monkeypatch.setattr(ans.abstention, "should_abstain", lambda *_a, **_k: False)
    ans.legacy_retrieve(_FakeConn(), CASE, "pago", plan=_plan(final_k=3))
    union_params = [p for sql, p in rows.calls if "UNION ALL" in sql]
    assert union_params and union_params[0]["k"] == 3


def test_ut_ans_13_payments_funnel_prefers_document_and_caps_audio(monkeypatch):
    """Regresión del incidente: una pregunta documental no se inunda de audio."""
    doc_id = uuid.uuid4()
    media_id = uuid.uuid4()
    doc = _chunk(document_id=doc_id, page_number=4, filename="demanda.pdf",
                 text="La demanda afirma que el pago se realizó el 13/12/2024", score=0.55)
    segments = [
        _chunk(document_id=None, page_number=None, media_id=media_id,
               start_ms=i * 60_000, end_ms=i * 60_000 + 4_000,
               text=f"En la audiencia se discute el pago, intervención {i}",
               metadata={"segment_id": f"s{i}", "speaker": "Alba"}, filename="audiencia.mp4",
               score=0.95)
        for i in range(6)
    ]
    _install(monkeypatch, _Rows([doc] + segments))
    monkeypatch.setattr(ans.abstention, "term_coverage", lambda *_a, **_k: 0.8)

    plan = _plan()
    items = ans.retrieve(_FakeConn(), CASE, "¿Qué evidencia hay sobre pagos?",
                         plan=plan, intent=intent_mod.Intent.LOOKUP)

    assert items[0]["document_id"] == doc_id
    assert sum(1 for it in items if it.get("media_id")) <= plan.max_per_file
    assert len(items) <= plan.final_k


def test_ut_ans_14_cache_key_is_stable_and_scope_aware():
    plan = _plan()
    key_a = ans._cache_key(CASE, "¿Qué evidencia hay sobre pagos?", plan, None, None, None, None)
    key_b = ans._cache_key(CASE, "¿Qué evidencia hay sobre pagos?", plan, None, None, None, None)
    key_c = ans._cache_key(CASE, "¿Qué evidencia hay sobre pagos?", plan, ["doc-1"], None, None, None)
    assert key_a == key_b
    assert key_a != key_c
