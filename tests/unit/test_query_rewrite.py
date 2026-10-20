"""UT-QRW — reescritura determinista de consulta y LLM opcional (contrato §1.3)."""
from types import SimpleNamespace

import pytest

from app.services import query_rewrite

pytestmark = pytest.mark.unit


def test_qrw_01_first_variant_is_the_original_question():
    question = "¿Qué evidencia hay sobre pagos?"
    variants = query_rewrite.rewrite(question)
    assert variants[0] == question


def test_qrw_02_adds_legal_synonyms():
    variants = query_rewrite.rewrite("¿Qué evidencia hay sobre pagos?")
    assert len(variants) > 1
    assert "comprobantes" in variants


def test_qrw_03_dedupes_variants():
    variants = query_rewrite.rewrite("pago")
    keys = [v.lower() for v in variants]
    assert len(keys) == len(set(keys))


def test_qrw_04_empty_question_returns_empty_list():
    assert query_rewrite.rewrite("   ") == []


def test_qrw_05_elliptical_question_uses_previous_turn():
    history = [{"role": "user", "content": "¿Qué pasó con el contrato?"},
               {"role": "assistant", "content": "El contrato se firmó en 2024."}]
    variants = query_rewrite.rewrite("¿y los pagos?", history=history)
    assert any("¿Qué pasó con el contrato?" in v and "¿y los pagos?" in v for v in variants)


def test_qrw_06_non_elliptical_question_ignores_history():
    history = [{"role": "user", "content": "¿Qué pasó con el contrato?"}]
    variants = query_rewrite.rewrite("¿Cuántos jueces intervinieron?", history=history)
    assert not any("contrato" in v for v in variants if v != variants[0])


def test_qrw_07_last_user_question_skips_assistant_turns():
    history = [{"role": "user", "content": "primera"},
               {"role": "assistant", "content": "respuesta"},
               {"role": "user", "content": "segunda"}]
    assert query_rewrite.last_user_question(history) == "segunda"


def test_qrw_08_history_accepts_plain_text_keys():
    history = [{"role": "user", "text": "pregunta vieja"}]
    assert query_rewrite.last_user_question(history) == "pregunta vieja"


class _LLM:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def complete(self, system: str, user: str):
        self.calls += 1
        return SimpleNamespace(text=self.text)


def test_qrw_09_llm_variants_are_used_when_routing_is_enabled(monkeypatch):
    monkeypatch.setattr(query_rewrite, "_routing_enabled", lambda: True)
    llm = _LLM('{"queries": ["paso en la audiencia de diciembre de 2024", "audiencia de diciembre", "diciembre"]}')
    variants = query_rewrite.rewrite("¿Qué pasó en la audiencia de diciembre?", llm=llm)
    assert llm.calls == 1
    assert "audiencia de diciembre" in variants
    assert variants[0] == "¿Qué pasó en la audiencia de diciembre?"


def test_qrw_10_llm_not_called_when_routing_is_disabled(monkeypatch):
    monkeypatch.setattr(query_rewrite, "_routing_enabled", lambda: False)
    llm = _LLM('{"queries": ["otra cosa"]}')
    query_rewrite.rewrite("pago honorarios", llm=llm)
    assert llm.calls == 0


def test_qrw_11_invalid_llm_json_falls_back(monkeypatch):
    monkeypatch.setattr(query_rewrite, "_routing_enabled", lambda: True)
    llm = _LLM("no soy json")
    variants = query_rewrite.rewrite("¿Qué pasó en la audiencia de diciembre?", llm=llm)
    assert llm.calls == 1
    assert variants[0] == "¿Qué pasó en la audiencia de diciembre?"
    assert "no soy json" not in variants


def test_qrw_12_llm_cannot_invent_numbers(monkeypatch):
    monkeypatch.setattr(query_rewrite, "_routing_enabled", lambda: True)
    llm = _LLM('{"queries": ["paso en la audiencia de 99.000.000", '
               '"paso en la audiencia", "audiencia de diciembre"]}')
    variants = query_rewrite.rewrite("¿Qué pasó en la audiencia de diciembre?", llm=llm)
    assert llm.calls == 1
    assert not any("99.000.000" in v for v in variants)
    assert "paso en la audiencia" in variants


def test_qrw_13_llm_variants_are_length_capped(monkeypatch):
    monkeypatch.setattr(query_rewrite, "_routing_enabled", lambda: True)
    llm = _LLM('{"queries": ["' + "x" * 400 + '"]}')
    variants = query_rewrite.rewrite("¿Qué pasó en la audiencia de diciembre?", llm=llm)
    assert llm.calls == 1
    assert all(len(v) <= query_rewrite.MAX_VARIANT_CHARS for v in variants)


def test_qrw_14_variant_count_is_capped():
    variants = query_rewrite.rewrite("pago contrato demanda sentencia apelación testigo perito")
    assert len(variants) <= query_rewrite.MAX_VARIANTS
