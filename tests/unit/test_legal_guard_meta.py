"""F4 · R2 — el gate legal usa el contexto enriquecido de la fuente.

T2 del contrato: A5–A7 con claims e ítems sintéticos. Sin base de datos: se
sustituyen `rows`/`one` y `citations.validate`, y se inyecta la lista de partes.
"""
from __future__ import annotations

import pytest

from app.services import legal_guard

pytestmark = pytest.mark.unit

VIDEO = "0065GrabacionAudiencia13Dic2024.mp4"
PARTY = {"id": "p1", "name": "Jorge Humberto Rojas Melo", "role": "defendant", "aliases": []}


@pytest.fixture
def fake_conn(monkeypatch):
    class Conn:
        pass

    monkeypatch.setattr(legal_guard, "one", lambda *a, **k: None)
    monkeypatch.setattr(legal_guard, "rows", lambda *a, **k: [])
    monkeypatch.setattr(legal_guard.citations_service, "validate", lambda *a, **k: (True, [], {}))
    monkeypatch.setattr(legal_guard, "_party_names", lambda *a, **k: [dict(PARTY)])
    return Conn()


def _policy():
    policy = legal_guard.default_policy("BALANCED")
    policy.update({"require_legal_norm": False, "block_on_numbers": True})
    return policy


def _segment(text: str, *, speaker: str = "SPEAKER_00", **extra) -> dict:
    return {"handle": "E1", "source_type": "transcript_segment", "media_id": "m1",
            "segment_id": "s1", "start_ms": 1000, "end_ms": 5000, "filename": VIDEO,
            "speaker": speaker, "text": text, **extra}


def _page(text: str, *, page_number: int = 1, **extra) -> dict:
    return {"handle": "E1", "source_type": "document_page", "document_id": "d1",
            "page_number": page_number, "filename": "0001 Expediente2018-361.pdf",
            "text": text, **extra}


def _codes(result: dict) -> set[str]:
    return {f["code"] for f in result["findings"]}


def test_a5_speaker_with_initials_supports_the_party(fake_conn):
    """A5: el hablante resuelto acredita la mención de la parte."""
    claim = "Jorge Humberto Rojas Melo rindió su nombre completo ante el despacho."
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": claim, "citations": ["E1"]}],
        items=[_segment("Damos inicio a la audiencia pública.", speaker="JORGE H. ROJAS M.")],
        policy=_policy())

    assert "PARTY_MISMATCH" not in _codes(result)
    assert result["claims"][0]["status"] == "SUPPORTED"


def test_a5_party_mismatch_stays_when_only_another_person_is_cited(fake_conn):
    claim = "Jorge Humberto Rojas Melo rindió su nombre completo ante el despacho."
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": claim, "citations": ["E1"]}],
        items=[_segment("Damos inicio a la audiencia pública.", speaker="CARLOS A. GÓMEZ G.")],
        policy=_policy())

    assert "PARTY_MISMATCH" in _codes(result)
    assert result["claims"][0]["missing_parties"] == ["Jorge Humberto Rojas Melo"]
    assert result["claims"][0]["status"] == "NEEDS_REVIEW"


def test_a6_date_in_the_video_filename_is_supported(fake_conn):
    """A6: la fecha del nombre del video respalda la fecha del claim."""
    claim = "El 13 de diciembre de 2024 se dio inicio a la audiencia pública."
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": claim, "citations": ["E1"]}],
        items=[_segment("Los demás intervinientes ya estamos grabando.")],
        policy=_policy())

    assert "DATE_MISMATCH" not in _codes(result)
    assert "NUMBER_MISMATCH" not in _codes(result)
    assert result["claims"][0]["status"] == "SUPPORTED"


def test_a6_other_date_still_mismatches(fake_conn):
    claim = "El 12 de mayo de 2025 se dio inicio a la audiencia pública."
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": claim, "citations": ["E1"]}],
        items=[_segment("Los demás intervinientes ya estamos grabando.")],
        policy=_policy())

    assert "DATE_MISMATCH" in _codes(result)
    assert result["claims"][0]["status"] == "UNSUPPORTED"


def test_a7_number_absent_from_the_cited_page_still_blocks(fake_conn):
    """A7: la regla de cifras no se relaja con el contexto enriquecido."""
    claim = "El valor total reclamado asciende a $6.336.800.000."
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": claim, "citations": ["E1"]}],
        items=[_page("Señor JUEZ 21 CIVIL DEL CIRCUITO DE BOGOTÁ. REF: PROCESO EJECUTIVO.")],
        policy=_policy())

    assert "NUMBER_MISMATCH" in _codes(result)
    assert result["claims"][0]["status"] == "UNSUPPORTED"
    assert result["approved_claims"] == []
    assert result["claims"][0]["missing_numbers"] == ["6336800000"]


def test_number_from_the_filename_or_page_is_legitimate(fake_conn):
    claim = "El proceso ejecutivo radicado 2018-361 cursa en el Juzgado 21 Civil."
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": claim, "citations": ["E1"]}],
        items=[_page("Señor JUEZ 21 CIVIL DEL CIRCUITO DE BOGOTÁ.", page_number=21)],
        policy=_policy())

    assert "NUMBER_MISMATCH" not in _codes(result)
    assert result["claims"][0]["status"] == "SUPPORTED"


def test_legal_norm_stated_by_the_source_is_review_not_blocked(fake_conn):
    """Si la fuente citada menciona la ley, no se borra: se marca para revisión."""
    policy = _policy()
    policy["require_legal_norm"] = True
    claim = "La parte demandante invocó la Ley 45 de 1990 para negar la usura."
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": claim, "citations": ["E1"]}],
        items=[_page("El apoderado sostuvo que la Ley 45 de 1990 exige el pago efectivo.")],
        policy=policy)

    assert "LEGAL_RULE_MISSING" in _codes(result)
    assert result["claims"][0]["status"] == "NEEDS_REVIEW"
    assert result["claims"][0]["legal_rule_ids"] == []


def test_legal_norm_absent_from_source_still_blocks(fake_conn):
    policy = _policy()
    policy["require_legal_norm"] = True
    claim = "La parte demandante invocó la Ley 45 de 1990 para negar la usura."
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": claim, "citations": ["E1"]}],
        items=[_page("El apoderado negó la usura sin citar norma alguna.")],
        policy=policy)

    assert "LEGAL_RULE_MISSING" in _codes(result)
    assert result["claims"][0]["status"] == "UNSUPPORTED"


def test_uuid_or_none_rejects_the_literal_none():
    assert legal_guard._uuid_or_none(None) is None
    assert legal_guard._uuid_or_none("None") is None
    assert legal_guard._uuid_or_none("no-es-uuid") is None
    assert legal_guard._uuid_or_none("3a9c36cb-6d59-44cf-8a6e-ec257391c64c") == \
        "3a9c36cb-6d59-44cf-8a6e-ec257391c64c"
