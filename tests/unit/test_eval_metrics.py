"""Métricas del runner de evaluación (capa B, sin DB ni LLM)."""
from __future__ import annotations

import pytest

from scripts.run_evals import (
    abstention_correct,
    answer_kind_match,
    citation_precision,
    coherence_checks,
    forbidden_hit,
    noise_ratio,
    percentile,
)

pytestmark = pytest.mark.unit

DEMANDA = {"filename": "01_demanda.pdf", "page_number": 1, "document_id": "d1", "source_type": "document_page"}
CONTESTACION = {"filename": "02_contestacion.pdf", "page_number": 1, "document_id": "d2", "source_type": "document_page"}
CORREO = {"filename": "04_anexo_correo.pdf", "page_number": 1, "document_id": "d4", "source_type": "document_page"}
AUDIENCIA = {"filename": "audiencia.mp4", "media_id": "m1", "segment_id": "s1", "start_ms": 4421000,
             "source_type": "transcript_segment"}


# ---------------------------------------------------------------- precision

def test_citation_precision_relevantes_sobre_usadas():
    used = [DEMANDA, CONTESTACION, CORREO]
    assert citation_precision(used, [{"filename_contains": "01_demanda"}, {"filename_contains": "02_contestacion"}], []) == pytest.approx(2 / 3)


def test_citation_precision_sin_citas_es_none():
    assert citation_precision([], [{"filename_contains": "01_demanda"}], []) is None


def test_citation_precision_sin_must_cite_usa_must_not_cite():
    assert citation_precision([DEMANDA, CORREO], [], [{"filename_contains": "04_anexo"}]) == pytest.approx(0.5)


def test_citation_precision_sin_criterio_es_none():
    assert citation_precision([DEMANDA], [], []) is None


# -------------------------------------------------------------------- noise

def test_noise_ratio_cuenta_irrelevantes_por_respuesta():
    used = [DEMANDA, CORREO, CORREO]
    assert noise_ratio(used, [{"filename_contains": "01_demanda"}], []) == 2.0


def test_noise_ratio_cero_con_todo_relevante():
    assert noise_ratio([DEMANDA], [{"filename_contains": "01_demanda"}], []) == 0.0


def test_noise_ratio_sin_criterio_es_none():
    assert noise_ratio([DEMANDA], [], []) is None


# -------------------------------------------------------------- prohibiciones

def test_forbidden_hit_detecta_afirmacion():
    assert forbidden_hit("El pago está probado judicialmente.", ["el pago está probado judicialmente"]) is True


def test_forbidden_hit_ignora_negacion_cercana():
    assert forbidden_hit("No puedo afirmar que el pago está probado judicialmente.",
                         ["el pago está probado judicialmente"]) is False


def test_forbidden_hit_sin_frases_es_false():
    assert forbidden_hit("cualquier respuesta", []) is False


# --------------------------------------------------------------- coherencia

def test_coherencia_abstencion_con_cita_falla():
    case = {"expected_abstention": True, "expected_answer_kind": "insufficient", "max_visible_citations": 0}
    result = {"answer_kind": "insufficient", "claims": [], "used_citations": [DEMANDA], "occurrences": []}
    checks = coherence_checks(case, result)
    assert checks["coherence_pass"] is False
    assert any("citas" in r for r in checks["coherence_reasons"])


def test_coherencia_abstencion_esperada_pero_responde_falla():
    case = {"expected_abstention": True, "expected_answer_kind": "insufficient"}
    result = {"answer_kind": "direct", "claims": [{"text": "x", "citations": ["E1"]}],
              "used_citations": [DEMANDA], "occurrences": []}
    assert coherence_checks(case, result)["coherence_pass"] is False


def test_coherencia_navegacional_con_claims_falla():
    case = {"expected_answer_kind": "navigational", "max_visible_citations": 4}
    result = {"answer_kind": "navigational", "claims": [{"text": "x", "citations": []}],
              "used_citations": [], "occurrences": []}
    assert coherence_checks(case, result)["coherence_pass"] is False


def test_coherencia_exceso_de_citas_falla():
    case = {"expected_answer_kind": "direct", "max_visible_citations": 2}
    result = {"answer_kind": "direct", "claims": [], "used_citations": [DEMANDA, CONTESTACION, CORREO],
              "occurrences": []}
    assert coherence_checks(case, result)["coherence_pass"] is False


def test_coherencia_duplicado_misma_pagina_falla():
    case = {"expected_answer_kind": "direct", "max_visible_citations": 4}
    dup = dict(DEMANDA)
    result = {"answer_kind": "direct", "claims": [], "used_citations": [DEMANDA, dup], "occurrences": []}
    assert coherence_checks(case, result)["coherence_pass"] is False


def test_coherencia_insufficient_limpia_pasa():
    case = {"expected_abstention": True, "expected_answer_kind": "insufficient", "max_visible_citations": 0}
    result = {"answer_kind": "insufficient", "claims": [], "used_citations": [], "occurrences": []}
    assert coherence_checks(case, result)["coherence_pass"] is True


# ------------------------------------------------------- abstención y kind

def test_abstention_correct_solo_aplica_a_casos_de_abstencion():
    ok = {"answer_kind": "insufficient", "used_citations": []}
    bad = {"answer_kind": "direct", "used_citations": [DEMANDA]}
    assert abstention_correct({"expected_abstention": True}, ok) is True
    assert abstention_correct({"expected_abstention": True}, bad) is False
    assert abstention_correct({"expected_answer_kind": "direct"}, bad) is True
    assert abstention_correct({"expected_answer_kind": "direct"}, ok) is False
    assert abstention_correct({}, ok) is None


def test_answer_kind_match_acepta_lista():
    assert answer_kind_match({"expected_answer_kind": "direct"}, {"answer_kind": "direct"}) is True
    assert answer_kind_match({"expected_answer_kind": ["partial", "insufficient"]}, {"answer_kind": "partial"}) is True
    assert answer_kind_match({"expected_answer_kind": "direct"}, {"answer_kind": "partial"}) is False
    assert answer_kind_match({}, {"answer_kind": "partial"}) is None


# ------------------------------------------------------------------ latency

def test_percentile_interpola_y_tolera_vacio():
    assert percentile([], 95) is None
    assert percentile([10.0], 95) == 10.0
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 50) == pytest.approx(50.5)
    assert percentile(values, 95) == pytest.approx(95.05)
