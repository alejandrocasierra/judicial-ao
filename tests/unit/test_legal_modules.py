"""Pruebas de los módulos legales: gate determinista, hechos y comparación."""
from __future__ import annotations

import pytest

from app.services import legal_guard, party_facts, second_opinion

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_conn(monkeypatch):
    class Conn:
        pass

    conn = Conn()
    monkeypatch.setattr(legal_guard, "one", lambda *a, **k: None)
    monkeypatch.setattr(legal_guard, "rows", lambda *a, **k: [])
    monkeypatch.setattr(legal_guard.citations_service, "validate",
                        lambda *a, **k: (True, [], {}))
    return conn


def _policy(**kwargs):
    p = legal_guard.default_policy("BALANCED")
    p.update({"require_legal_norm": False, "block_on_numbers": True})
    p.update(kwargs)
    return p


def test_legal_guard_extracts_norms_and_dates():
    norms = legal_guard._extract_norms("El artículo 123 del Código Civil y la Ley 1564 de 2012.")
    assert any(n["kind"] == "article" and n["identifier"] == "123" for n in norms)
    assert any(n["kind"] == "law" and "1564" in n["identifier"] for n in norms)
    dates = legal_guard._extract_dates("El pago fue el 1 de febrero de 2019 y el otro 02/03/2020.")
    assert len(dates) == 2


def test_legal_guard_blocks_number_not_in_citation(fake_conn):
    item = {"handle": "E1", "source_type": "document_page", "document_id": "d",
            "page_number": 1, "text": "El pagaré se entregó por 8.400.000 pesos."}
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": "El pagaré se entregó por 9.400.000 pesos.", "citations": ["E1"]}],
        items=[item], policy=_policy())
    assert result["status"] == "BLOCKED"
    assert result["approved_claims"] == []
    assert any(f["code"] == "NUMBER_MISMATCH" for f in result["findings"])


def test_legal_guard_accepts_grounded_claim(fake_conn):
    item = {"handle": "E1", "source_type": "document_page", "document_id": "d",
            "page_number": 1, "text": "El pagaré se entregó por 8.400.000 pesos."}
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": "El pagaré se entregó por 8.400.000 pesos.", "citations": ["E1"]}],
        items=[item], policy=_policy())
    assert result["status"] == "PASSED"
    assert len(result["approved_claims"]) == 1


def test_facts_side_mapping():
    assert party_facts.side_for_role("claimant") == "DEMANDANTE"
    assert party_facts.side_for_role("defendant") == "DEMANDADO"
    assert party_facts.side_for_role("third_party") == "TERCERO"
    assert party_facts.side_for_role(None) == "TERCERO"


def test_second_opinion_compare_agree():
    claims = [{"text": "El pagaré se entregó el 1 de febrero de 2019 por 8.400.000."}]
    out = second_opinion.compare(claims, claims)
    assert out["verdict"] == "AGREE"
    assert out["matches"] == 1


def test_second_opinion_compare_number_disagrees():
    out = second_opinion.compare(
        [{"text": "El pagaré se entregó por 8.400.000."}],
        [{"text": "El pagaré se entregó por 9.400.000."}],
    )
    assert out["verdict"] == "DISAGREE"
    assert any(d["type"] == "DIFFERENT_NUMBER" for d in out["differences"])


def test_second_opinion_compare_missing_is_partial():
    out = second_opinion.compare(
        [{"text": "El pagaré se entregó el 1 de febrero de 2019."}],
        [],
    )
    assert out["verdict"] in {"PARTIAL", "INCONCLUSIVE"}
