"""UT-ANS — recuperación híbrida FTS + vector + RRF."""
import uuid
from typing import Any

import pytest

from app.services import answering as ans

pytestmark = pytest.mark.unit

CASE = str(uuid.uuid4())


def _make_rows(data: list[dict[str, Any]]):
    def _rows(_c, _s, **params):
        return list(data)
    return _rows


def test_ut_ans_01_rrf_fuses_disjoint_lists():
    a = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    b = [{"id": "c"}, {"id": "d"}]
    fused = ans._rrf_fuse([a, b], k=10, rrf_k=60)
    ids = [it["id"] for it in fused]
    # c aparece en ambas listas -> debe ganar.
    assert ids[0] == "c"
    assert set(ids) == {"a", "b", "c", "d"}


def test_ut_ans_02_retrieve_uses_chunks(monkeypatch):
    chunks = [
        {"id": uuid.uuid4(), "document_id": uuid.uuid4(), "page_number": 5,
         "media_id": None, "start_ms": None, "end_ms": None,
         "text": "El comprobante de pago", "metadata": {"folio": "12"},
         "filename": "doc.pdf", "score": 0.9},
    ]

    def _rows(_c, _s, **params):
        # La primera llamada es la construcción de q_lex.
        if "unnest" in _s:
            return [{"q": "pago | comprob"}]
        return list(chunks)

    monkeypatch.setattr(ans, "rows", _rows)
    monkeypatch.setattr(ans, "get_embedding_provider", lambda: _FakeProvider())
    monkeypatch.setattr(ans.abstention, "should_abstain", lambda _c, _case, _q, _min=None: False)
    items = ans.retrieve(_FakeConn(has_chunks=True), CASE, "pruebas del pago")
    assert len(items) == 1
    assert items[0]["source_type"] == "document_page"
    assert items[0]["folio"] == "12"
    assert items[0]["handle"] == "E1"


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
