"""Evaluador puro del set dorado de investigación (F0 / R3).

Solo funciones puras: no hay red, ni base de datos, ni API. Las respuestas son
sintéticas para probar cada criterio de forma aislada y determinista.
"""
from __future__ import annotations

import pytest

from scripts.eval_golden_chat import evaluate_case, evaluate_cases, normalize, render_markdown

pytestmark = pytest.mark.unit

RESPUESTA_OK = (
    "En el proceso hay 15 pagarés. El principal es 77391469 y el segundo P-78213828. "
    "La demandante los relaciona en el cuaderno de demanda ejecutiva acumulada."
)


def _caso(**overrides) -> dict:
    caso = {
        "id": "INV-01",
        "question": "¿Cuántos pagarés hay en el proceso?",
        "expected_answer_kind": "direct",
        "must_contain": ["15", "77391469"],
        "must_cite_any": [
            {"filename_contains": "CuadernoDemandaEjecutivaAcumulada2018-361.pdf"},
            {"filename_contains": "AutoAdmiteAcumulacionyLibraMandamientodePago2018-361.pdf"},
        ],
        "min_citations": 2,
        "min_length": 20,
        "required_headings": [],
        "forbidden_phrases": ["Encontré material relacionado", "Se omitieron afirmaciones"],
    }
    caso.update(overrides)
    return caso


def _resultado(**overrides) -> dict:
    resultado = {
        "answer": RESPUESTA_OK,
        "answer_kind": "direct",
        "intent": "dato",
        "citations_detail": [
            {"filename": "CuadernoDemandaEjecutivaAcumulada2018-361.pdf", "page": 12},
            {"filename": "AutoAdmiteAcumulacionyLibraMandamientodePago2018-361.pdf", "page": 3},
        ],
        "claims": [{"text": "hay 15 pagarés"}],
        "uncertainties": [],
        "degraded": False,
        "elapsed": 42.0,
    }
    resultado.update(overrides)
    return resultado


def _criterios(evaluacion: dict) -> dict[str, dict]:
    return {c["id"]: c for c in evaluacion["criteria"]}


def _fallidos(evaluacion: dict) -> list[str]:
    return [c["id"] for c in evaluacion["criteria"] if not c["passed"]]


# ------------------------------------------------------------------ normalización
def test_normalize_quita_tildes_y_mayusculas():
    assert normalize("CAPITALIZACIÓN de Intereses") == "capitalizacion de intereses"
    assert normalize("  Pagaré   Nº 15 ") == "pagare no 15"
    assert normalize("") == ""


def test_must_contain_ignora_tildes_y_mayusculas():
    caso = _caso(must_contain=["CAPITALIZACIÓN", "Pagaré", "SÍNTESIS"])
    resultado = _resultado(answer="La capitalizacion de intereses, el pagare y la sintesis del caso.")

    evaluacion = evaluate_case(caso, resultado)
    criterios = _criterios(evaluacion)

    for termino in ("CAPITALIZACIÓN", "Pagaré", "SÍNTESIS"):
        assert criterios[f"must_contain:{termino}"]["passed"] is True, termino


def test_must_contain_numerico_no_pasa_por_un_numero_mas_largo():
    """«15» no puede satisfacerse con el 0015 de un nombre de archivo."""
    caso = _caso(must_contain=["15"], must_cite_any=[], min_citations=0)
    resultado = _resultado(answer="Ver 0015 EscritoContestacion y $415.000.000.")

    evaluacion = evaluate_case(caso, resultado)
    assert _criterios(evaluacion)["must_contain:15"]["passed"] is False

    resultado_ok = _resultado(answer="Se cobran 15 pagarés en el Juzgado 21.")
    assert _criterios(evaluate_case(caso, resultado_ok))["must_contain:15"]["passed"] is True


# ------------------------------------------------------------------ caso en verde
def test_caso_que_cumple_todo_pasa():
    evaluacion = evaluate_case(_caso(), _resultado())

    assert evaluacion["passed"] is True, _fallidos(evaluacion)
    assert evaluacion["failed"] == []
    assert all(c["passed"] for c in evaluacion["criteria"])


# ------------------------------------------------------------------- caso en rojo
def test_sin_15_falla_el_criterio_exacto():
    resultado = _resultado(answer="El proceso tiene varios pagarés y el principal es 77391469.")

    evaluacion = evaluate_case(_caso(), resultado)
    criterios = _criterios(evaluacion)

    assert criterios["must_contain:15"]["passed"] is False
    assert "must_contain:15" in _fallidos(evaluacion)
    assert evaluacion["passed"] is False


def test_cartel_prohibido_hace_fallar_el_caso():
    resultado = _resultado(answer="Encontré material relacionado. " + RESPUESTA_OK)

    evaluacion = evaluate_case(_caso(), resultado)
    criterios = _criterios(evaluacion)

    assert criterios["forbidden_phrases"]["passed"] is False
    assert "Encontré material relacionado" in criterios["forbidden_phrases"]["detail"]
    assert evaluacion["passed"] is False


def test_truncamiento_de_afirmaciones_tambien_es_prohibido():
    resultado = _resultado(answer=RESPUESTA_OK + " Se omitieron afirmaciones sin cita.")

    evaluacion = evaluate_case(_caso(), resultado)
    assert _criterios(evaluacion)["forbidden_phrases"]["passed"] is False


# ------------------------------------------------------------------------ citas
def test_must_cite_any_basta_con_una_fuente_de_la_lista():
    resultado = _resultado(citations_detail=[{"filename": "AutoAdmiteAcumulacionyLibraMandamientodePago2018-361.pdf"}])

    criterios = _criterios(evaluate_case(_caso(min_citations=1), resultado))
    assert criterios["must_cite_any"]["passed"] is True


def test_must_cite_any_falla_si_ninguna_fuente_coincide():
    resultado = _resultado(citations_detail=[{"filename": "OtroDocumento2019.pdf"}])

    evaluacion = evaluate_case(_caso(), resultado)
    criterio = _criterios(evaluacion)["must_cite_any"]

    assert criterio["passed"] is False
    assert "OtroDocumento2019.pdf" in criterio["detail"]
    assert evaluacion["passed"] is False


def test_min_citations_falla_con_menos_fuentes():
    criterios = _criterios(evaluate_case(_caso(min_citations=3), _resultado()))
    assert criterios["min_citations"]["passed"] is False
    assert "2" in criterios["min_citations"]["detail"]


def test_cita_por_segmento_de_transcripcion():
    caso = _caso(
        must_cite_any=[{"filename_contains": "GrabacionAudiencia13Dic2024"}],
        min_citations=1,
    )
    resultado = _resultado(citations_detail=[{
        "filename": "GrabacionAudiencia13Dic2024.mp4",
        "start_ms": 120000,
        "source_type": "transcript_segment",
    }])

    assert _criterios(evaluate_case(caso, resultado))["must_cite_any"]["passed"] is True


# ------------------------------------------------------------------- longitud etc.
def test_min_length_falla_con_respuesta_corta():
    criterios = _criterios(evaluate_case(_caso(min_length=500), _resultado()))
    assert criterios["min_length"]["passed"] is False
    assert "500" in criterios["min_length"]["detail"]


def test_required_headings_exige_cada_encabezado():
    caso = _caso(required_headings=["Síntesis", "Demandada"])
    resultado = _resultado(answer="## SÍNTESIS\nHay 15 pagarés (77391469).")

    evaluacion = evaluate_case(caso, resultado)
    criterios = _criterios(evaluacion)

    assert criterios["required_headings:Síntesis"]["passed"] is True
    assert criterios["required_headings:Demandada"]["passed"] is False
    assert evaluacion["passed"] is False


def test_answer_kind_distinto_falla():
    criterios = _criterios(evaluate_case(_caso(), _resultado(answer_kind="insufficient")))
    assert criterios["answer_kind"]["passed"] is False


# ------------------------------------------------------------- fallos de ejecución
def test_error_de_transporte_no_aborta_y_marca_fallo():
    evaluacion = evaluate_case(_caso(), {"error": "timeout tras 240 s", "timeout": True})

    assert evaluacion["passed"] is False
    assert _criterios(evaluacion)["respuesta"]["passed"] is False
    assert "timeout" in _criterios(evaluacion)["respuesta"]["detail"]


def test_el_runner_mide_y_sigue_con_todos_los_casos():
    casos = [_caso(), _caso(id="INV-02", must_contain=["886"])]
    resultados = [
        _resultado(),
        {"error": "send:500", "answer": ""},
    ]

    informe = evaluate_cases(casos, resultados)

    assert [c["id"] for c in informe["cases"]] == ["INV-01", "INV-02"]
    assert informe["summary"]["passed"] == 1
    assert informe["summary"]["failed"] == 1
    assert informe["passed"] is False


# ------------------------------------------------------------------------- informe
def test_markdown_muestra_pass_fail_por_criterio_y_diagnostico():
    informe = evaluate_cases([_caso()], [_resultado()])
    inf = {
        "label": "before",
        "api": "http://127.0.0.1:8000/v1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "case_ref": "11001310302120180036100",
        "case_id": "38865959-8d5f-44e4-bbc8-1226bd345b00",
        "summary": informe["summary"],
        "cases": informe["cases"],
    }

    markdown = render_markdown(inf)

    assert "INV-01" in markdown
    assert "PASS" in markdown
    assert "| Criterio |" in markdown
    assert "Diagnóstico" in markdown
    assert "must_contain:15" in markdown
