"""UT-IDX — indexación de chunks y embeddings."""
import uuid
from typing import Any

import pytest

from app.services import indexing

pytestmark = pytest.mark.unit

ORG = str(uuid.uuid4())
CASE = str(uuid.uuid4())
DOC = str(uuid.uuid4())


class _FakeConn:
    def __init__(self):
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def execute(self, clause, params=None):
        sql = str(clause)
        self.executed.append((sql, params or {}))
        return _FakeResult([])


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeProvider:
    name = "fake"
    dimensions = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def test_ut_idx_01_index_document_deletes_and_inserts(monkeypatch):
    chunk = {
        "organization_id": ORG,
        "case_id": CASE,
        "chunk_type": "document_section",
        "document_id": DOC,
        "page_number": 1,
        "media_id": None,
        "start_ms": None,
        "end_ms": None,
        "text": "Página de prueba",
        "metadata": {"folio": "1"},
    }
    monkeypatch.setattr(indexing, "get_embedding_provider", lambda: _FakeProvider())
    monkeypatch.setattr(indexing.chunking, "chunk_document", lambda _c, _o, _ca, _d: [chunk])

    inserted: list[tuple[list[dict], list[list[float]]]] = []

    def _fake_insert(_conn, chunks, vectors, model, version):
        inserted.append((chunks, vectors))
        return len(chunks)

    monkeypatch.setattr(indexing, "_insert_chunks", _fake_insert)

    conn = _FakeConn()
    result = indexing.index_document(conn, ORG, CASE, DOC)
    assert result["chunks"] == 1
    assert any("DELETE FROM chunks" in sql for sql, _ in conn.executed)
    assert len(inserted) == 1
    assert inserted[0][0][0]["document_id"] == DOC


def test_ut_idx_02_vector_literal_format():
    assert indexing._vector_literal([0.0, 1.0]) == "[0.0,1.0]"
