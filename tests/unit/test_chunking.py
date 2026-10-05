"""UT-CHK — chunking jurídico."""
import uuid
from typing import Any

import pytest

from app.services import chunking as chk

pytestmark = pytest.mark.unit

ORG = str(uuid.uuid4())
CASE = str(uuid.uuid4())
DOC = str(uuid.uuid4())
MEDIA = str(uuid.uuid4())


def _make_rows(data: list[dict[str, Any]], extras: dict[str, list[dict]] | None = None):
    extras = extras or {}
    def _rows(_c, sql: str, **params):
        for key, rows_ in extras.items():
            if key in sql:
                return list(rows_)
        return list(data)
    return _rows


def test_ut_chk_01_document_indexa_ambos_modos_por_pagina(monkeypatch):
    versions = [
        {"page_number": 1, "mode": "basico", "text": "Texto página uno (básico)."},
        {"page_number": 1, "mode": "document_ai", "text": "Texto página uno (AI)."},
        {"page_number": 2, "mode": "basico", "text": "Texto página dos (básico)."},
        {"page_number": 2, "mode": "document_ai", "text": "Texto página dos (AI)."},
    ]
    folios = [{"page_number": 1, "folio": "1"}, {"page_number": 2, "folio": "2"}]

    def _rows(_c, sql: str, **params):
        if "document_ocr_versions" in sql:
            return list(versions)
        if "document_pages" in sql:
            return list(folios)
        return []  # entities

    monkeypatch.setattr(chk, "rows", _rows)
    chunks = chk.chunk_document(object(), ORG, CASE, DOC)

    assert len(chunks) == 4  # 2 páginas × 2 modos
    assert {c["metadata"]["ocr_mode"] for c in chunks} == {"basico", "document_ai"}
    assert chunks[0]["chunk_type"] == "document_section"
    assert chunks[0]["metadata"]["folio"] == "1"
    assert chunks[0]["document_id"] == DOC


def test_ut_chk_02_document_omite_paginas_vacias(monkeypatch):
    versions = [
        {"page_number": 1, "mode": "basico", "text": "   "},
        {"page_number": 1, "mode": "document_ai", "text": ""},
        {"page_number": 2, "mode": "document_ai", "text": "Solo esta."},
    ]

    def _rows(_c, sql: str, **params):
        if "document_ocr_versions" in sql:
            return list(versions)
        if "document_pages" in sql:
            return [{"page_number": 1, "folio": "1"}, {"page_number": 2, "folio": "2"}]
        return []

    monkeypatch.setattr(chk, "rows", _rows)
    chunks = chk.chunk_document(object(), ORG, CASE, DOC)
    assert len(chunks) == 1
    assert chunks[0]["page_number"] == 2
    assert chunks[0]["metadata"]["ocr_mode"] == "document_ai"


def test_ut_chk_03_media_groups_same_speaker(monkeypatch):
    speaker_id = str(uuid.uuid4())
    segs = [
        {"id": uuid.uuid4(), "speaker_id": speaker_id, "start_ms": 0, "end_ms": 5000, "text": "Primera parte."},
        {"id": uuid.uuid4(), "speaker_id": speaker_id, "start_ms": 6000, "end_ms": 9000, "text": "Segunda parte."},
    ]
    def _rows(_c, sql: str, **params):
        if "speakers" in sql:
            return [{"label": "SPEAKER_00"}]
        if "entities" in sql:
            return []
        return list(segs)
    monkeypatch.setattr(chk, "rows", _rows)
    chunks = chk.chunk_media(object(), ORG, CASE, MEDIA)
    assert len(chunks) == 1
    assert "Primera parte." in chunks[0]["text"]
    assert "Segunda parte." in chunks[0]["text"]
    assert chunks[0]["metadata"]["speaker"] == "SPEAKER_00"


def test_ut_chk_04_media_splits_on_large_gap(monkeypatch):
    speaker_id = str(uuid.uuid4())
    segs = [
        {"id": uuid.uuid4(), "speaker_id": speaker_id, "start_ms": 0, "end_ms": 5000, "text": "A"},
        {"id": uuid.uuid4(), "speaker_id": speaker_id, "start_ms": 40000, "end_ms": 45000, "text": "B"},
    ]
    def _rows(_c, sql: str, **params):
        if "speakers" in sql:
            return [{"label": "SPEAKER_00"}]
        if "entities" in sql:
            return []
        return list(segs)
    monkeypatch.setattr(chk, "rows", _rows)
    chunks = chk.chunk_media(object(), ORG, CASE, MEDIA)
    assert len(chunks) == 2


def test_ut_chk_06_media_prefers_display_name(monkeypatch):
    """Al renombrar un hablante, la reindexación de pgvector usa su display_name."""
    speaker_id = str(uuid.uuid4())
    segs = [{"id": uuid.uuid4(), "speaker_id": speaker_id, "start_ms": 0, "end_ms": 5000, "text": "Hola."}]

    def _rows(_c, sql: str, **params):
        if "speakers" in sql:
            return [{"label": "SPK-01", "display_name": "Álvaro Lúzico Álvarez"}]
        if "entities" in sql:
            return []
        return list(segs)

    monkeypatch.setattr(chk, "rows", _rows)
    chunks = chk.chunk_media(object(), ORG, CASE, MEDIA)
    assert chunks[0]["metadata"]["speaker"] == "Álvaro Lúzico Álvarez"


def test_ut_chk_05_claims_skip_empty_text(monkeypatch):
    claims = [
        {"id": uuid.uuid4(), "text": "", "claim_type": "party_assertion", "confidence": 0.9},
        {"id": uuid.uuid4(), "text": "Sí hay texto", "claim_type": "documentary_statement", "confidence": 0.8},
    ]
    monkeypatch.setattr(chk, "rows", _make_rows(claims))
    chunks = chk.chunk_claims(object(), ORG, CASE)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Sí hay texto"
    assert chunks[0]["metadata"]["claim_type"] == "documentary_statement"
