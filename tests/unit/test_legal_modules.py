"""Pruebas de los módulos legales: gate determinista, hechos y comparación."""
from __future__ import annotations

import pytest

from app.services import legal_guard, party_facts, second_opinion

pytestmark = pytest.mark.unit

CGP_422 = {
    "id": "11111111-1111-1111-1111-111111111111",
    "source": "Ley 1564 de 2012 - Código General del Proceso",
    "identifier": "422",
    "title": "Artículo 422",
    "text": (
        "Artículo 422. Título ejecutivo. Pueden demandarse ejecutivamente las "
        "obligaciones expresas, claras y exigibles que consten en documentos que "
        "provengan del deudor o de su causante, y constituyan plena prueba contra "
        "él, o las que emanen de una sentencia de condena."
    ),
}


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


def _item(text: str, **extra):
    return {"handle": "E1", "source_type": "document_page", "document_id": "d",
            "page_number": 1, "text": text, **extra}


def _rule_patch(monkeypatch, rule):
    monkeypatch.setattr(legal_guard, "_lookup_legal_rule",
                        lambda *a, **k: rule)


def test_legal_guard_extracts_norms_and_dates():
    norms = legal_guard._extract_norms("El artículo 123 del Código Civil y la Ley 1564 de 2012.")
    assert any(n["kind"] == "article" and n["identifier"] == "123" for n in norms)
    assert any(n["kind"] == "law" and "1564" in n["identifier"] for n in norms)
    dates = legal_guard._extract_dates("El pago fue el 1 de febrero de 2019 y el otro 02/03/2020.")
    assert len(dates) == 2


def _article_ids(text: str) -> list[str]:
    return [n["identifier"] for n in legal_guard._extract_norms(text)
            if n["kind"] == "article"]


def test_legal_guard_preserves_hyphenated_article_identifiers():
    assert _article_ids("El artículo 839-01 del Estatuto Tributario.") == ["839-01"]
    assert _article_ids("El artículo 839-1 del Estatuto Tributario.") == ["839-1"]
    assert _article_ids("El artículo 784-5 del Estatuto Tributario.") == ["784-5"]
    assert legal_guard._identifier_key("839-01") == "83901"
    assert legal_guard._identifier_key("839-1") == "8391"
    assert legal_guard._identifier_key("839-01") != legal_guard._identifier_key("839-1")


def test_legal_guard_preserves_dotted_article_identifiers():
    assert _article_ids("El artículo 2.2.6.1.2.4.1 del Decreto 1069 de 2015.") == ["2.2.6.1.2.4.1"]


def test_legal_guard_expands_article_ranges_without_touching_normal_article():
    expected = [str(n) for n in range(10, 17)]
    assert _article_ids("Los artículos 10-16 del Código General del Proceso.") == expected
    assert _article_ids("Los artículos 10 a 16 del Código General del Proceso.") == expected
    assert _article_ids("Los artículos 10 al 16 del Código General del Proceso.") == expected
    assert _article_ids("Los artículos 10 y 16 del Código General del Proceso.") == ["10", "16"]
    assert _article_ids("El artículo 422 del Código General del Proceso.") == ["422"]
    assert _article_ids("El artículo 10-5 no es un rango de dos dígitos.") == ["10-5"]



def test_legal_guard_blocks_number_not_in_citation(fake_conn):
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": "El pagaré se entregó por 9.400.000 pesos.", "citations": ["E1"]}],
        items=[_item("El pagaré se entregó por 8.400.000 pesos.")], policy=_policy())
    assert result["status"] == "BLOCKED"
    assert result["approved_claims"] == []
    assert any(f["code"] == "NUMBER_MISMATCH" for f in result["findings"])


def test_legal_guard_accepts_grounded_claim(fake_conn):
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": "El pagaré se entregó por 8.400.000 pesos.", "citations": ["E1"]}],
        items=[_item("El pagaré se entregó por 8.400.000 pesos.")], policy=_policy())
    assert result["status"] == "PASSED"
    assert len(result["approved_claims"]) == 1


def test_gate_semantic_article_supports_claim(fake_conn, monkeypatch):
    _rule_patch(monkeypatch, CGP_422)
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": ("El artículo 422 del Código General del Proceso permite demandar "
                          "ejecutivamente las obligaciones expresas, claras y exigibles que "
                          "consten en documentos que provengan del deudor."),
                 "citations": ["E1"]}],
        items=[_item("El pagaré consta en un documento firmado por el deudor.")],
        policy=_policy(require_legal_norm=True))
    assert result["status"] == "PASSED", result
    assert result["claims"][0]["status"] == "SUPPORTED"
    assert result["claims"][0]["legal_rule_ids"] == [CGP_422["id"]]


def test_gate_semantic_blocks_false_consequence(fake_conn, monkeypatch):
    _rule_patch(monkeypatch, CGP_422)
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": ("El artículo 422 del Código General del Proceso declara la "
                          "nulidad absoluta del pagaré por falta de registro."),
                 "citations": ["E1"]}],
        items=[_item("El pagaré consta en un documento firmado por el deudor.")],
        policy=_policy(require_legal_norm=True))
    assert any(f["code"] == "LEGAL_CONSEQUENCE_UNSUPPORTED" for f in result["findings"])
    assert result["claims"][0]["status"] == "UNSUPPORTED"
    assert result["approved_claims"] == []
    assert result["status"] == "BLOCKED"


def test_gate_semantic_blocks_missing_article(fake_conn, monkeypatch):
    _rule_patch(monkeypatch, None)
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": "El artículo 9999 del Código General del Proceso lo permite.",
                 "citations": ["E1"]}],
        items=[_item("El pagaré consta en un documento firmado por el deudor.")],
        policy=_policy(require_legal_norm=True))
    assert any(f["code"] == "LEGAL_RULE_MISSING" for f in result["findings"])
    assert result["status"] == "BLOCKED"


def test_gate_range_fails_closed_when_one_article_is_missing(fake_conn, monkeypatch):
    def lookup(conn, org_id, jurisdiction, norm, claim_text=""):
        if norm["kind"] != "article":
            return None
        if norm["identifier"] == "16":
            return None
        return {
            "id": f"rule-{norm['identifier']}",
            "source": "Ley 1564 de 2012 - Código General del Proceso",
            "identifier": norm["identifier"],
            "title": f"Artículo {norm['identifier']}",
            "text": (f"Artículo {norm['identifier']}. Los artículos regulan la prueba "
                     "en el proceso general."),
        }

    monkeypatch.setattr(legal_guard, "_lookup_legal_rule", lookup)
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": ("Los artículos 10 a 16 del Código General del Proceso "
                          "regulan la prueba."), "citations": ["E1"]}],
        items=[_item("El pagaré consta en un documento firmado por el deudor.")],
        policy=_policy(require_legal_norm=True))
    missing = [f for f in result["findings"] if f["code"] == "LEGAL_RULE_MISSING"]
    assert missing, result["findings"]
    assert missing[0]["metadata"]["norm"]["identifier"] == "16"
    assert result["status"] == "BLOCKED"


def test_gate_quote_hash_mismatch_blocks(fake_conn):
    quote = "El pagaré se entregó por 8.400.000 pesos."
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": "El pagaré se entregó por 8.400.000 pesos.", "citations": ["E1"],
                 "quote_hash": "0" * 64}],
        items=[_item(quote, quote=quote, quote_hash="0" * 64)], policy=_policy())
    assert any(f["code"] == "QUOTE_MISMATCH" for f in result["findings"])
    assert result["status"] == "BLOCKED"


def test_gate_quote_hash_verified_passes(fake_conn):
    quote = "El pagaré se entregó por 8.400.000 pesos."
    digest = legal_guard.citations_service.quote_hash(quote)
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": "El pagaré se entregó por 8.400.000 pesos.", "citations": ["E1"],
                 "quote_hash": digest}],
        items=[_item(quote, quote=quote, quote_hash=digest)], policy=_policy())
    assert not any(f["code"] == "QUOTE_MISMATCH" for f in result["findings"])
    assert result["status"] == "PASSED"


def test_gate_open_contradiction_omitted_requires_review(fake_conn, monkeypatch):
    monkeypatch.setattr(legal_guard, "_open_contradictions", lambda *a, **k: [{
        "id": "co-1", "description": "Entrega del pagaré", "severity": "high",
        "review_status": "PENDING",
        "text_a": "El pagaré se entregó el 1 de febrero de 2019.",
        "text_b": "El pagaré nunca se entregó.",
    }])
    result = legal_guard.audit(
        fake_conn, org_id="o", case_id="c", question="q", answer="",
        claims=[{"text": "El pagaré se entregó el 1 de febrero de 2019.", "citations": ["E1"]}],
        items=[_item("El pagaré se entregó el 1 de febrero de 2019.")], policy=_policy())
    assert any(f["code"] == "CONTRADICTION_OMITTED" for f in result["findings"])
    assert result["claims"][0]["status"] == "NEEDS_REVIEW"
    assert result["claims"][0]["contradictions"][0]["contradiction_id"] == "co-1"
    assert result["status"] in {"WARNING", "NEEDS_REVIEW"}


def test_judge_cannot_approve_deterministic_rejection():
    result = {
        "claims": [{"claim_index": 0, "text": "El artículo 422 permite todo.",
                    "claim_type": "LEGAL_CONCLUSION", "citations": ["E1"],
                    "status": "UNSUPPORTED", "legal_rule_ids": [CGP_422["id"]],
                    "reasons": []}],
        "approved_claims": [], "findings": [], "policy": {"mode": "BALANCED"},
        "claims_supported": 0, "claims_blocked": 1, "findings_critical": 0,
    }
    legal_guard.apply_judge(result, [{
        "claim_index": 0, "status": "SUPPORTED", "reason": "parece correcto",
        "evidence_ids": ["N1"], "auditor": "JUDGE"}])
    assert result["claims"][0]["status"] == "UNSUPPORTED"
    assert result["approved_claims"] == []
    assert result["judge_claims"][0]["judge_status"] == "SUPPORTED"


def test_judge_rejection_blocks_supported_claim():
    result = {
        "claims": [{"claim_index": 0, "text": "El artículo 422 permite todo.",
                    "claim_type": "LEGAL_CONCLUSION", "citations": ["E1"],
                    "status": "SUPPORTED", "legal_rule_ids": [CGP_422["id"]],
                    "reasons": []}],
        "approved_claims": [{"text": "El artículo 422 permite todo.", "citations": ["E1"]}],
        "findings": [], "policy": {"mode": "BALANCED"},
        "claims_supported": 1, "claims_blocked": 0, "findings_critical": 0,
    }
    legal_guard.apply_judge(result, [{
        "claim_index": 0, "status": "NOT_SUPPORTED", "reason": "el artículo no dice eso",
        "evidence_ids": ["N1"], "auditor": "JUDGE"}])
    assert result["claims"][0]["status"] == "UNSUPPORTED"
    assert result["status"] == "BLOCKED"
    assert any(f["code"] == "JUDGE_NOT_SUPPORTED" for f in result["findings"])


def test_facts_side_mapping():
    assert party_facts.side_for_role("claimant") == "DEMANDANTE"
    assert party_facts.side_for_role("defendant") == "DEMANDADO"
    assert party_facts.side_for_role("third_party") == "TERCERO"
    assert party_facts.side_for_role(None) == "TERCERO"


def test_facts_stance_requires_explicit_attribution():
    assert party_facts.infer_stance(
        "Admito que recibí el pago.", party_name="Ana",
        speaker_party_id="p1", target_party_id="p1") == "ADMITS"
    assert party_facts.infer_stance(
        "Ana admite que recibió el pago.", party_name="Ana",
        speaker_party_id="p1", target_party_id="p1") == "ADMITS"
    assert party_facts.infer_stance(
        "El demandado admite que recibió el pago.", party_name="Ana",
        speaker_party_id="p1", target_party_id="p1") == "UNKNOWN"
    assert party_facts.infer_stance(
        "El pagaré se entregó el 1 de febrero.", party_name="Ana",
        speaker_party_id="p1", target_party_id="p1") == "UNKNOWN"
    assert party_facts.infer_stance(
        "No niego que exista el pagaré.", party_name="Ana",
        speaker_party_id="p1", target_party_id="p1") == "UNKNOWN"
    assert party_facts.infer_stance(
        "Niega haber recibido el pago.", party_name="Ana",
        speaker_party_id="p1", target_party_id="p1") == "DENIES"
    assert party_facts.infer_stance(
        "Sostiene que el pago fue parcial.", party_name="Ana",
        speaker_party_id="p1", target_party_id="p1") == "ALLEGES"
    assert party_facts.infer_stance(
        "Admito el pago.", party_name="Ana",
        speaker_party_id="p1", target_party_id="p2") == "UNKNOWN"


def test_facts_proof_flag_blocks_only_promotions(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(party_facts, "get_settings",
                        lambda: SimpleNamespace(FACTS_PROOF_ENABLED=False))
    fact = {"proof_status": "ALLEGED"}
    with pytest.raises(party_facts.ProofStatusDisabled):
        party_facts._assert_proof_transition_allowed(fact, "ADMITTED")
    with pytest.raises(party_facts.ProofStatusDisabled):
        party_facts._assert_proof_transition_allowed(fact, "JUDICIALLY_PROVEN")
    # Estados no probatorios y re-guardar el mismo estado siguen permitidos.
    party_facts._assert_proof_transition_allowed(fact, "DISPUTED")
    party_facts._assert_proof_transition_allowed({"proof_status": "ADMITTED"}, "ADMITTED")

    monkeypatch.setattr(party_facts, "get_settings",
                        lambda: SimpleNamespace(FACTS_PROOF_ENABLED=True))
    party_facts._assert_proof_transition_allowed(fact, "ADMITTED")


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


class _FakeJudge:
    def __init__(self, payload: str | None = None, fail: bool = False):
        self.payload = payload
        self.fail = fail
        self.tokens_in = 100
        self.tokens_out = 40

    def complete(self, system, user):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("provider down")
        from types import SimpleNamespace
        return SimpleNamespace(text=self.payload or "{}", tokens_in=self.tokens_in,
                               tokens_out=self.tokens_out)


def test_second_opinion_llm_comparator_parses_and_persists_diffs():
    original = [{"text": "El pagaré se entregó por 8.400.000."}]
    second = [{"text": "El pagaré se entregó por 8.400.000."}]
    llm = _FakeJudge('```json\n{"verdict":"AGREE","differences":[{"claim_index":0,'
                     '"type":"MATCH","reason":"misma cifra"}]}\n```')
    out = second_opinion.compare_with_llm(original, second, llm, "verifier")
    assert out["mode"] == "LLM"
    assert out["verdict"] == "AGREE"
    assert out["comparison_tokens_in"] == 100


def test_second_opinion_llm_cannot_hide_numeric_difference():
    original = [{"text": "El pagaré se entregó por 8.400.000."}]
    second = [{"text": "El pagaré se entregó por 9.400.000."}]
    llm = _FakeJudge('{"verdict":"AGREE","differences":[{"claim_index":0,"type":"MATCH"}]}')
    out = second_opinion.compare_with_llm(original, second, llm, "verifier")
    assert out["verdict"] == "DISAGREE"
    assert any(d["type"] == "DIFFERENT_NUMBER" for d in out["differences"])


def test_second_opinion_llm_failure_falls_back_deterministic():
    out = second_opinion.compare_with_llm(
        [{"text": "El pagaré se entregó."}], [{"text": "El pagaré se entregó."}],
        _FakeJudge(fail=True), "verifier")
    assert out["mode"] == "DETERMINISTIC"
    assert out["fallback_reason"].startswith("llm_error")


def test_second_opinion_cost_uses_model_price_or_estimates():
    priced = {"input_price_per_mtok": 1.0, "output_price_per_mtok": 2.0}
    cost, source = second_opinion.cost_for_tokens(priced, 1_000_000, 500_000)
    assert cost == 2.0 and source == "MODEL_PRICE"
    cost2, source2 = second_opinion.cost_for_tokens({}, 1_000_000, 1_000_000)
    assert source2 == "ESTIMATED" and cost2 > 0


def test_second_opinion_auto_policy():
    assert second_opinion.auto_should_run("NEVER", "analisis", "PASSED") is None
    assert second_opinion.auto_should_run("ANALYSIS", "analisis", "PASSED") == "ANALYSIS"
    assert second_opinion.auto_should_run("ANALYSIS", "dato", "PASSED") is None
    assert second_opinion.auto_should_run("ALL", "dato", "BLOCKED") == "ALL"
    assert second_opinion.auto_should_run("STRICT_ONLY", "dato", "WARNING") == "STRICT_ONLY"
    assert second_opinion.auto_should_run("STRICT_ONLY", "dato", "PASSED") is None
    assert second_opinion.auto_should_run("NEVER", "dato", "PASSED", required=True) == "REQUIRED"
