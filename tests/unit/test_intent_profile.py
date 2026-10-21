"""Perfil de intención por usuario: voto, puertas y política de activación.

El riesgo de este módulo no es que no acierte: es que un perfil malo reabra el
bug histórico (pregunta sustantiva respondida con una lista de menciones). Por
eso lo que más se prueba son las PUERTAS, no el voto.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import intent, intent_learning, intent_profile
from app.services.intent import Intent, IntentDecision

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def _base(intent_value=Intent.LOOKUP, confidence=0.45, reason="default_lookup") -> IntentDecision:
    return IntentDecision(intent_value, confidence, reason, None, "rules")


def _neighbor(label: str, similarity: float = 0.9, source: str = "feedback",
              weight: float = 1.0, days: int = 0) -> dict:
    return {"label": label, "similarity": similarity, "source": source, "weight": weight,
            "created_at": NOW - timedelta(days=days)}


# ------------------------------------------------------------------- vectores

def test_cosine_tolera_vectores_no_normalizados():
    assert intent_profile.cosine([1, 0], [0, 1]) == 0.0
    assert intent_profile.cosine([1, 0], [5, 0]) == pytest.approx(1.0)
    assert intent_profile.cosine([1, 1], [1, 0]) == pytest.approx(0.7071, abs=1e-3)
    assert intent_profile.cosine([], [1]) == 0.0


def test_recencia_decae_a_la_mitad_en_90_dias():
    assert intent_profile.recency_weight(NOW, NOW) == 1.0
    assert intent_profile.recency_weight(NOW - timedelta(days=90), NOW) == pytest.approx(0.5)
    assert intent_profile.recency_weight(NOW - timedelta(days=180), NOW) == pytest.approx(0.25)
    # Una fecha futura (reloj desfasado) no amplifica el peso.
    assert intent_profile.recency_weight(NOW + timedelta(days=3), NOW) == 1.0


# ---------------------------------------------------------------------- voto

def test_voto_necesita_dos_vecinos():
    assert intent_profile.vote([_neighbor("navegar")], NOW) is None
    assert intent_profile.vote([], NOW) is None


def test_voto_ignora_vecinos_poco_similares():
    found = [_neighbor("navegar", similarity=0.3), _neighbor("navegar", similarity=0.2)]
    assert intent_profile.vote(found, NOW) is None


def test_el_feedback_pesa_mas_que_el_contrafactual():
    found = [_neighbor("analisis", source="feedback", weight=1.0, similarity=0.8),
             _neighbor("dato", source="counterfactual", weight=0.4, similarity=0.8)]
    result = intent_profile.vote(found, NOW)
    assert result is not None and result.label == "analisis"


def test_voto_empatado_tiene_margen_cero():
    found = [_neighbor("dato", similarity=0.8), _neighbor("analisis", similarity=0.8)]
    assert intent_profile.vote(found, NOW).margin == pytest.approx(0.0)


# -------------------------------------------------------------------- puertas

def test_regla_fuerte_no_se_toca():
    decision = _base(Intent.AGGREGATION, 0.9, "count_or_who_role")
    vote = intent_profile.Vote("dato", 0.9, 1.0, 4)
    assert intent_profile.apply_gates(decision, vote, "¿Cuántos testigos hay?") is decision


def test_adjuntos_no_se_tocan():
    decision = _base(Intent.READ, 0.85, "attachments_present")
    vote = intent_profile.Vote("dato", 0.9, 1.0, 4)
    assert intent_profile.apply_gates(decision, vote, "¿qué dice?") is decision


def test_margen_bajo_no_decide():
    decision = _base()
    vote = intent_profile.Vote("analisis", 0.05, 1.0, 4)
    assert intent_profile.apply_gates(decision, vote, "háblame del pago") is decision


def test_perfil_no_puede_inventar_navegar():
    """El bug caro: responder con timestamps una pregunta sustantiva."""
    decision = _base()
    vote = intent_profile.Vote("navegar", 0.8, 1.0, 4)
    out = intent_profile.apply_gates(decision, vote, "¿Qué contradictorio es el pago?")
    assert out is decision
    assert out.intent is Intent.LOOKUP


def test_perfil_si_puede_elegir_navegar_con_senal_explicita():
    decision = _base()
    vote = intent_profile.Vote("navegar", 0.8, 1.0, 4)
    out = intent_profile.apply_gates(decision, vote, "¿Dónde aparece la cláusula de pago?")
    assert out.intent is Intent.NAVIGATE
    assert out.source == "profile"
    assert out.confidence <= 0.85


def test_el_perfil_tampoco_inventa_ubicacion_abstracta():
    decision = _base()
    vote = intent_profile.Vote("navegar", 0.9, 1.0, 4)
    out = intent_profile.apply_gates(decision, vote, "¿Dónde está la contradicción del pago?")
    assert out is decision
    assert not intent.has_location_signal("¿Dónde está la contradicción del pago?")
    assert not intent.has_location_signal("Localiza el nombre del perito")


def test_perfil_nunca_decide_correccion():
    decision = _base()
    vote = intent_profile.Vote("correccion", 0.9, 1.0, 4)
    assert intent_profile.apply_gates(decision, vote, "el hablante es otro").intent is Intent.LOOKUP


def test_perfil_rescata_el_default():
    decision = _base()
    vote = intent_profile.Vote("agregacion", 0.6, 1.0, 4)
    out = intent_profile.apply_gates(decision, vote, "dame los nombres de los que declararon")
    assert out.intent is Intent.AGGREGATION
    assert out.reason == "user_profile" and out.source == "profile"


# ------------------------------------------------------------------ política

def test_sin_embeddings_semanticos_nunca_activa():
    status, reason = intent_profile.status_for(
        judged=100, exemplars=50, rules_accuracy=0.6, profile_accuracy=0.95,
        fp_rules=2, fp_profile=0, semantic_embeddings=False)
    assert status == "shadow" and reason == "embedding_provider_not_semantic"


def test_sin_datos_queda_apagado():
    status, reason = intent_profile.status_for(
        judged=0, exemplars=0, rules_accuracy=None, profile_accuracy=None,
        fp_rules=0, fp_profile=0, semantic_embeddings=True)
    assert status == "off" and reason == "no_data"


def test_pocos_veredictos_no_activan():
    status, reason = intent_profile.status_for(
        judged=3, exemplars=10, rules_accuracy=0.4, profile_accuracy=0.9,
        fp_rules=1, fp_profile=0, semantic_embeddings=True)
    assert status == "shadow" and reason == "not_enough_feedback"


def test_mas_falsos_positivos_de_navegar_bloquean_la_activacion():
    status, reason = intent_profile.status_for(
        judged=30, exemplars=30, rules_accuracy=0.5, profile_accuracy=0.9,
        fp_rules=1, fp_profile=3, semantic_embeddings=True)
    assert status == "shadow" and reason == "profile_would_add_navegar_fp"


def test_mejora_clara_activa():
    status, reason = intent_profile.status_for(
        judged=30, exemplars=30, rules_accuracy=0.6, profile_accuracy=0.8,
        fp_rules=2, fp_profile=1, semantic_embeddings=True)
    assert status == "active" and reason == "profile_beats_rules"


def test_empeorar_apaga_el_perfil():
    status, reason = intent_profile.status_for(
        judged=30, exemplars=30, rules_accuracy=0.8, profile_accuracy=0.5,
        fp_rules=1, fp_profile=1, semantic_embeddings=True)
    assert status == "off" and reason == "profile_worse_than_rules"


# --------------------------------------------------------------- derivación

def test_util_confirma_la_etiqueta_que_se_juzgaba():
    specs, forget = intent_learning.derive_exemplars(
        [{"user_id": "u1", "verdict": "accepted", "router_intent": "navegar",
          "question": "¿En qué minuto se habla de pagos?", "created_at": NOW}], [])
    assert len(specs) == 1 and specs[0].label == "navegar" and specs[0].source == "feedback"
    assert specs[0].weight == 1.0 and forget == {}


def test_no_era_esto_con_intencion_aporta_la_verdad():
    specs, _ = intent_learning.derive_exemplars(
        [{"user_id": "u1", "verdict": "wrong_intent", "corrected_intent": "dato",
          "router_intent": "navegar", "question": "¿Dónde está la contradicción?",
          "created_at": NOW}], [])
    assert specs[0].label == "dato"


def test_rechazo_sin_intencion_no_inventa_pero_olvida():
    specs, forget = intent_learning.derive_exemplars(
        [{"user_id": "u1", "verdict": "rejected", "router_intent": "navegar",
          "question": "Localiza la fecha de la audiencia", "created_at": NOW}], [])
    assert specs == []
    assert forget == {"u1": [intent_learning.question_hash("Localiza la fecha de la audiencia")]}


def test_reformulacion_exitosa_entra_con_peso_bajo():
    specs, _ = intent_learning.derive_exemplars(
        [], [{"user_id": "u1", "question": "dame los nombres de los que declararon",
              "intent": "agregacion", "created_at": NOW}])
    assert specs[0].source == "counterfactual" and specs[0].weight == 0.4


def test_etiquetas_invalidas_se_descartan():
    specs, forget = intent_learning.derive_exemplars(
        [{"user_id": "u1", "verdict": "wrong_intent", "corrected_intent": "resumen",
          "question": "x", "created_at": NOW}], [])
    assert specs == [] and forget == {}


def test_hash_es_estable_y_normaliza():
    assert (intent_learning.question_hash("¿Dónde está el PAGO?")
            == intent_learning.question_hash("donde esta el pago"))


def test_la_etiqueta_del_router_actual_se_usa_para_util():
    assert intent_learning.label_from_feedback(
        {"verdict": "accepted", "router_intent": "dato"}) == "dato"
    assert intent_learning.label_from_feedback(
        {"verdict": "accepted", "router_intent": None}) is None
    assert intent_learning.label_from_feedback({"verdict": "rejected"}) is None


def test_un_util_sobre_el_perfil_no_entrena_al_perfil():
    """Aceptar la decisión del propio perfil no es verdad independiente."""
    assert intent_learning.label_from_feedback(
        {"verdict": "accepted", "router_intent": "dato", "router_source": "profile"}) is None
    assert intent_learning.label_from_feedback(
        {"verdict": "accepted", "router_intent": "dato", "router_source": "llm"}) is None
    assert intent_learning.label_from_feedback(
        {"verdict": "wrong_intent", "corrected_intent": "dato",
         "router_source": "profile"}) == "dato"


def test_el_ultimo_veredicto_manda_aunque_la_pregunta_se_repita():
    older = {"user_id": "u1", "verdict": "rejected",
             "question": "¿Qué pasó con el pago?", "created_at": NOW - timedelta(days=1)}
    newer = {"user_id": "u1", "verdict": "accepted", "router_intent": "dato",
             "question": "¿Qué pasó con el pago?", "created_at": NOW}
    specs, forget = intent_learning.derive_exemplars([newer, older], [])
    assert len(specs) == 1 and specs[0].label == "dato"
    assert forget.get("u1", []) == []

    specs, forget = intent_learning.derive_exemplars([older, newer], [])
    assert len(specs) == 1 and specs[0].label == "dato"


def test_el_perfil_no_importa_servicios_de_arranque():
    """Sanity: el módulo expone lo mínimo y no toca el router real."""
    assert intent.ROUTER_VERSION
    assert intent_profile.STRONG_RULE_CONFIDENCE > intent_profile.PROFILE_MARGIN_MIN


def test_la_misma_pregunta_juzgada_dos_veces_la_gana_el_veredicto_nuevo():
    specs, _ = intent_learning.derive_exemplars([
        {"user_id": "u1", "verdict": "wrong_intent", "corrected_intent": "dato",
         "question": "¿Qué pasó con el pago?", "created_at": NOW - timedelta(days=2)},
        {"user_id": "u1", "verdict": "accepted", "router_intent": "analisis",
         "question": "¿Qué pasó con el pago?", "created_at": NOW},
    ], [])
    assert len(specs) == 1
    assert specs[0].label == "analisis"


class _SemanticStub:
    """Espacio semántico de juguete: cada significado tiene su vector."""

    name = "stub-semantic"
    _GROUPS = (
        ("¿Cuándo se firmó el contrato?", "¿En qué fecha se suscribió el contrato?"),
        ("¿Quién compareció a la audiencia?", "¿Qué personas asistieron a la audiencia?"),
        ("¿Cuánto se pagó?", "¿Qué monto se pagó?"),
        ("¿Dónde aparece la cláusula de pago?", "¿En qué página está la cláusula de pago?"),
        ("¿Quién firmó el contrato?",),
        ("¿Dónde se menciona el pago?",),
        ("¿Qué dijo el testigo?",),
        ("¿Cuántos testigos declararon?",),
    )

    def embed(self, texts):
        index = {}
        for i, items in enumerate(self._GROUPS):
            for text in items:
                index[text] = i
        vectors = []
        for text in texts:
            vec = [0.0] * len(self._GROUPS)
            vec[index.get(text, len(self._GROUPS) - 1)] = 1.0
            vectors.append(vec)
        return vectors


def test_la_sonda_detecta_un_espacio_semantico():
    result = intent_profile.probe_embeddings(_SemanticStub())
    assert result["semantic"] is True
    assert result["same"] > result["different"]


def test_la_sonda_rechaza_el_hash_aleatorio():
    from app.core.config import get_settings
    from app.providers.embeddings import FakeEmbedding

    assert intent_profile.probe_embeddings(FakeEmbedding(get_settings()))["semantic"] is False


def test_la_sonda_rechaza_el_hash_lexico():
    from app.core.config import get_settings
    from app.providers.embeddings import LocalNgramEmbedding

    assert intent_profile.probe_embeddings(LocalNgramEmbedding(get_settings()))["semantic"] is False


def test_la_alerta_solo_usa_http_y_no_rompe(monkeypatch):
    from types import SimpleNamespace

    sent: list[dict] = []

    class _Response:
        def close(self):
            return None

    def fake_urlopen(request, timeout=0):
        sent.append({"url": request.full_url, "body": request.data, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(intent_learning, "get_settings",
                        lambda: SimpleNamespace(INTENT_ALERT_WEBHOOK="https://hooks.example/x"))
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    intent_learning.notify_degradation("org", "user", old="active", new="shadow",
                                       reason="profile_worse_than_rules", metrics_data={"judged": 12})
    assert len(sent) == 1 and sent[0]["url"] == "https://hooks.example/x"
    assert b"intent_profile_degraded" in sent[0]["body"]

    sent.clear()
    monkeypatch.setattr(intent_learning, "get_settings",
                        lambda: SimpleNamespace(INTENT_ALERT_WEBHOOK="file:///etc/passwd"))
    intent_learning.notify_degradation("org", "user", old="active", new="off",
                                       reason="x", metrics_data={})
    assert sent == []
