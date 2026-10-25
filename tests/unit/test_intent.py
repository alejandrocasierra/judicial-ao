"""UT-INT — router de intención determinista (contrato §1.2)."""
from types import SimpleNamespace

import pytest

from app.services import intent, text_terms

pytestmark = pytest.mark.unit

INT = intent.Intent


@pytest.mark.parametrize("question,expected", [
    # dato: la pregunta del incidente y preguntas sustantivas
    ("¿Qué evidencia hay sobre pagos?", INT.LOOKUP),
    ("¿Qué dice la demanda sobre el pago?", INT.LOOKUP),
    ("¿Cuál es el nombre del juez?", INT.LOOKUP),
    ("¿Cuánto se pagó?", INT.LOOKUP),
    ("¿Quién firmó la sentencia?", INT.LOOKUP),
    ("¿El pago aparece en la demanda?", INT.LOOKUP),  # "aparece" solo NO es navegar
    ("¿Qué pruebas existen del pago?", INT.LOOKUP),
    # navegar: señales explícitas de ubicación
    ("¿Dónde aparece el pagaré?", INT.NAVIGATE),
    ("¿Dónde se habla de pagos?", INT.NAVIGATE),
    ("¿En qué minuto se menciona el pagaré?", INT.NAVIGATE),
    ("¿En qué página está el comprobante?", INT.NAVIGATE),
    ("¿A qué minuto empieza la audiencia?", INT.NAVIGATE),
    ("busca la palabra transferencia", INT.NAVIGATE),
    ("localiza el comprobante", INT.NAVIGATE),
    ("dame el timestamp del pago", INT.NAVIGATE),
    # agregación
    ("¿Cuántos jueces intervinieron?", INT.AGGREGATION),
    ("¿Cuántos testigos declararon?", INT.AGGREGATION),
    ("¿Quiénes son los apoderados?", INT.AGGREGATION),
    ("¿Cuántas partes hay?", INT.AGGREGATION),
    ("¿Cuántos peritos hay?", INT.AGGREGATION),
    ("¿Quiénes intervinieron en la audiencia?", INT.AGGREGATION),
    # procedimental
    ("¿Por qué se llegó a la sentencia?", INT.PROCEDURAL),
    ("¿Qué causó la terminación del contrato?", INT.PROCEDURAL),
    ("¿Qué actuación originó el embargo?", INT.PROCEDURAL),
    ("dame los antecedentes procesales", INT.PROCEDURAL),
    ("explícame la cadena procesal", INT.PROCEDURAL),
    # análisis
    ("¿Qué implica la contradicción entre la demanda y el testigo?", INT.ANALYSIS),
    ("compara la demanda con la contestación", INT.ANALYSIS),
    ("analiza las fortalezas de la demanda", INT.ANALYSIS),
    ("evalúa la coherencia del testimonio", INT.ANALYSIS),
    ("¿Cuál es la relación entre el pago y el contrato?", INT.ANALYSIS),
    # lectura
    ("Lee este documento y resúmelo", INT.READ),
    ("resume este archivo", INT.READ),
    # corrección
    ("corrige el hablante", INT.CORRECTION),
    ("el hablante es Alba Lucy Cock", INT.CORRECTION),
    ("corrige: no es Alba sino Pedro", INT.CORRECTION),
])
def test_int_01_classifies_questions(question, expected):
    decision = intent.classify(question)
    assert decision.intent is expected, f"{question!r} -> {decision}"
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.source == "rules"


def test_int_02_nav_term_only_for_navigate():
    nav = intent.classify("¿Dónde aparece el pagaré 001?")
    assert nav.nav_term == "pagare 001"
    assert nav.nav_term == text_terms.specific_term("¿Dónde aparece el pagaré 001?")

    lookup = intent.classify("¿Qué evidencia hay sobre pagos?")
    assert lookup.nav_term is None


def test_int_03_payments_question_is_not_navigation():
    """Regresión del incidente: la captura de «pagos» fue un fallo de routing."""
    decision = intent.classify("¿Qué evidencia hay sobre pagos?")
    assert decision.intent is INT.LOOKUP
    assert decision.intent is not INT.NAVIGATE


def test_int_04_attachments_are_read():
    decision = intent.classify("¿qué dice?", has_attachments=True)
    assert decision.intent is INT.READ
    assert decision.reason == "attachments_present"


def test_int_05_attachments_do_not_beat_explicit_location():
    decision = intent.classify("¿en qué página aparece el pago?", has_attachments=True)
    assert decision.intent is INT.NAVIGATE


def test_int_06_correction_flag_override():
    decision = intent.classify("lo que sea", is_correction=True)
    assert decision.intent is INT.CORRECTION
    assert decision.source == "override"
    assert decision.confidence == 1.0


def test_int_07_empty_question_defaults_to_lookup():
    decision = intent.classify("")
    assert decision.intent is INT.LOOKUP
    assert decision.source == "rules"


def test_int_08_reason_never_contains_user_text():
    question = "¿Qué evidencia hay sobre el pagaré 001 de la demanda?"
    decision = intent.classify(question)
    assert text_terms.norm(question)[:24] not in text_terms.norm(decision.reason)


class _LLM:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def complete(self, system: str, user: str):
        self.calls += 1
        assert "intent" in system
        return SimpleNamespace(text=self.text)


def test_int_09_llm_revises_low_confidence_decision():
    llm = _LLM('{"intent": "analisis", "confidence": 0.8, "reason": "requires reasoning"}')
    decision = intent.classify("háblame del pagaré", llm=llm)
    assert llm.calls == 1
    assert decision.intent is INT.ANALYSIS
    assert decision.source == "llm"
    assert decision.reason == "requires reasoning"


def test_int_10_llm_cannot_invent_navigation():
    """El LLM no puede convertir una pregunta sustantiva en una lista de menciones."""
    llm = _LLM('{"intent": "navegar", "confidence": 0.7, "reason": "location"}')
    decision = intent.classify("háblame del pagaré 001", llm=llm)
    assert llm.calls == 1
    assert decision.intent is INT.LOOKUP
    assert decision.source == "rules"


def test_int_11_invalid_llm_json_falls_back_to_rules():
    llm = _LLM("no soy json")
    decision = intent.classify("háblame del pagaré", llm=llm)
    assert decision.intent is INT.LOOKUP
    assert decision.source == "rules"


def test_int_12_llm_unknown_intent_falls_back_to_rules():
    llm = _LLM('{"intent": "inventado", "confidence": 0.9}')
    decision = intent.classify("háblame del pagaré", llm=llm)
    assert decision.intent is INT.LOOKUP
    assert decision.source == "rules"


def test_int_13_llm_not_called_for_confident_rule():
    llm = _LLM('{"intent": "lectura", "confidence": 0.9}')
    decision = intent.classify("¿Cuántos jueces intervinieron?", llm=llm)
    assert decision.intent is INT.AGGREGATION
    assert llm.calls == 0


def test_int_14_llm_reason_cannot_leak_question_text():
    question = "háblame del pagaré 001 de la demanda"
    llm = _LLM('{"intent": "dato", "confidence": 0.7, "reason": "háblame del pagaré 001 de la demanda"}')
    decision = intent.classify(question, llm=llm)
    assert decision.reason == "llm_router"


def test_int_15_llm_accepts_enum_name_and_clamps_confidence():
    llm = _LLM('{"intent": "LOOKUP", "confidence": 7, "reason": "hecho"}')
    decision = intent.classify("háblame del pagaré", llm=llm)
    assert decision.intent is INT.LOOKUP
    assert decision.confidence == 1.0
    assert decision.source == "llm"


def test_int_17_hints_can_name_an_intent():
    decision = intent.classify("háblame de eso", hints=["navegar"])
    assert decision.intent is INT.NAVIGATE
    assert decision.reason == "caller_hint"


def test_int_18_hints_do_not_override_explicit_rules():
    decision = intent.classify("¿Cuántos jueces intervinieron?", hints=["lectura"])
    assert decision.intent is INT.AGGREGATION


def test_int_19_read_verb_is_not_triggered_by_a_third_person_verb():
    decision = intent.classify("¿Qué lee la demanda?")
    assert decision.intent is INT.LOOKUP


def test_int_20_helpers():
    assert intent.is_navigation(intent.classify("localiza el pago"))
    assert not intent.is_navigation(intent.classify("¿Qué evidencia hay sobre pagos?"))
    assert intent.uses_graph(intent.classify("¿Qué evidencia hay sobre pagos?"))
    assert intent.uses_graph(intent.classify("compara la demanda y la contestación"))
    assert not intent.uses_graph(intent.classify("¿Cuántos jueces intervinieron?"))


def test_int_21_followup_inherits_previous_intent():
    """«¿Y en la página 12?» no se sostiene sola: hereda el turno anterior."""
    history = [{"role": "user", "content": "compara la demanda"},
               {"role": "assistant", "intent": "analisis"}]
    decision = intent.classify("¿Y en la página 12?", history=history)
    assert decision.intent is INT.ANALYSIS
    assert decision.reason == "followup_inherit"
    assert decision.confidence < 0.9
    assert not intent.has_location_signal("¿Y en la página 12?")


def test_int_22_followup_never_inherits_navigation_without_location_signal():
    history = [{"role": "assistant", "intent": "navegar"}]
    decision = intent.classify("¿y eso?", history=history)
    assert decision.intent is INT.LOOKUP
    assert decision.reason == "default_lookup"


def test_int_23_strong_rule_beats_history():
    history = [{"role": "assistant", "intent": "analisis"}]
    decision = intent.classify("¿Cuántos testigos declararon?", history=history)
    assert decision.intent is INT.AGGREGATION
    assert decision.confidence == 0.9


def test_int_24_without_history_there_is_no_inheritance():
    decision = intent.classify("¿Y en la página 12?")
    assert decision.intent is INT.LOOKUP
    assert decision.reason == "default_lookup"


def test_int_25_llm_reviews_ambiguous_multiple_matches():
    question = "corrige el hablante y compara los testimonios"
    base = intent.classify(question)
    assert base.confidence == 0.62  # dos familias: corrección + análisis
    llm = _LLM('{"intent": "analisis", "confidence": 0.8, "reason": "compara"}')
    decision = intent.classify(question, llm=llm)
    assert llm.calls == 1
    assert decision.intent is INT.ANALYSIS
    assert decision.source == "llm"


def test_int_26_llm_never_reviews_attachments():
    llm = _LLM('{"intent": "dato", "confidence": 0.9}')
    decision = intent.classify("¿qué dice?", has_attachments=True, llm=llm)
    assert decision.intent is INT.READ
    assert llm.calls == 0


def test_int_27_llm_low_confidence_is_ignored():
    llm = _LLM('{"intent": "analisis", "confidence": 0.2}')
    decision = intent.classify("háblame del pagaré", llm=llm)
    assert decision.intent is INT.LOOKUP
    assert decision.source == "rules"


def test_int_28_llm_cannot_decide_correction():
    llm = _LLM('{"intent": "correccion", "confidence": 0.9}')
    decision = intent.classify("háblame del pagaré", llm=llm)
    assert decision.intent is INT.LOOKUP
    assert decision.source == "rules"


def test_int_29_los_guardarrailes_de_precision_siguen_en_pie():
    """Las dos clases caras no se disparan con una pregunta sustantiva."""
    assert intent.classify(
        "¿Dónde está la contradicción entre la demanda y la contestación?").intent is INT.ANALYSIS
    assert intent.classify("Localiza la fecha de la audiencia en el expediente.").intent is INT.LOOKUP
    assert intent.classify(
        "Corrige mi entendimiento: ¿el pago fue en mayo o en marzo?").intent is INT.LOOKUP
    assert intent.classify("¿Está equivocado el perito?").intent is INT.LOOKUP
    assert not intent.has_location_signal("¿Dónde está la contradicción?")
    assert not intent.has_location_signal("Localiza el nombre del perito")
    assert intent.has_location_signal("¿Dónde aparece el pagaré 001?")
    assert intent.classify("Estabas equivocado: el pago fue en mayo.").intent is INT.CORRECTION


class _SlowLLM:
    def complete(self, system: str, user: str):
        import time

        time.sleep(0.3)
        return '{"intent": "analisis", "confidence": 0.8}'


def test_int_30_el_l2_tiene_corte_duro_y_no_bloquea_el_turno(monkeypatch):
    from concurrent.futures import TimeoutError as FuturesTimeout

    with pytest.raises(FuturesTimeout):
        intent._call_with_timeout(_SlowLLM(), "s", "u", 0.05)

    monkeypatch.setattr(intent, "_call_with_timeout",
                        lambda *a, **k: (_ for _ in ()).throw(FuturesTimeout()))
    decision = intent.classify("háblame del pagaré", llm=_SlowLLM())
    assert decision.intent is INT.LOOKUP
    assert decision.source == "rules"


# --- F1: preguntas serias al camino serio -----------------------------------


def test_int_31_usura_es_analisis():
    """A1: el fondo jurídico-probatorio no puede quedarse en `dato`."""
    decision = intent.classify("¿Se cobraron intereses de usura?")
    assert decision.intent is INT.ANALYSIS
    assert decision.reason == "analysis_verb"


def test_int_32_inventario_de_pagares_es_analisis():
    """A2: un inventario sustantivo no es una lista de roles ni un dato suelto."""
    decision = intent.classify("¿Cuántos pagarés hay en el proceso?")
    assert decision.intent is INT.ANALYSIS
    assert decision.reason == "substantive_inventory"
    assert decision.confidence == 0.85


def test_int_33_pregunta_inv_03_escala_a_analisis():
    """A3: la pregunta completa del set dorado abandona el RAG de 10 fragmentos."""
    question = (
        "busque que dice cada parte sobre los intereses de usura que lleve a determinar "
        "si se cobraron intereses de usura o no y adicional revisa en la documentacion, "
        "interregatorios, hechos que llegen a responder esta pregunta sobre el cobro de intereses"
    )
    decision = intent.classify(question)
    assert decision.intent is INT.ANALYSIS
    assert decision.reason in ("multi_source_escalation", "analysis_verb")


def test_int_34_peritos_siguen_siendo_agregacion():
    """A4: los roles de personas no son inventarios sustantivos."""
    assert intent.classify("¿Cuántos peritos hay?").intent is INT.AGGREGATION


def test_int_35_evidencia_sobre_pagos_sigue_siendo_dato():
    """A5: «pagos» no es «pagarés»; la pregunta del incidente no se toca."""
    assert intent.classify("¿Qué evidencia hay sobre pagos?").intent is INT.LOOKUP


def test_int_36_identidad_sigue_siendo_dato():
    """A6: preguntar por una persona no es investigar el fondo."""
    assert intent.classify("¿Quién es Jorge Rojas?").intent is INT.LOOKUP


def test_int_37_documentos_del_expediente_siguen_siendo_agregacion():
    """A7: los documentos/anexos conservan su ruta de agregación."""
    assert intent.classify("¿Cuántos documentos conforman el expediente?").intent is INT.AGGREGATION


def test_int_38_escalamiento_por_dos_fuentes_sin_verbo_de_analisis():
    decision = intent.classify("¿Qué dicen los documentos y los interrogatorios sobre el pago?")
    assert decision.intent is INT.ANALYSIS
    assert decision.reason == "multi_source_escalation"
    assert decision.confidence == 0.8


def test_int_39_escalamiento_por_longitud_y_conector():
    question = (
        "Quiero que revises la demanda y adicional me digas si el pago de mayo se hizo "
        "antes de la audiencia de diciembre del año pasado"
    )
    decision = intent.classify(question)
    assert decision.intent is INT.ANALYSIS
    assert decision.reason == "multi_source_escalation"


def test_int_40_pregunta_corta_de_una_sola_fuente_no_escala():
    decision = intent.classify("¿Qué dice la demanda sobre el pago?")
    assert decision.intent is INT.LOOKUP
    assert decision.reason != "multi_source_escalation"


def test_int_41_identidad_no_escala_aunque_mencione_varias_fuentes():
    decision = intent.classify("¿Quién es Jorge Rojas en la demanda y en los interrogatorios?")
    assert decision.intent is INT.LOOKUP
    assert decision.reason != "multi_source_escalation"


def test_int_42_el_escalamiento_es_idempotente():
    already = intent.IntentDecision(INT.ANALYSIS, 0.9, "analysis_verb", None, "rules")
    result = intent._escalate_multi_source(
        "¿Qué dicen los documentos y los interrogatorios sobre el pago?", already)
    assert result is already


def test_int_43_router_version_actual():
    assert intent.ROUTER_VERSION == "rules-2026.12"
