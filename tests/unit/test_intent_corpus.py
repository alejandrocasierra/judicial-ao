"""Arnés de medición del router de intención (corpus + métricas, sin DB ni LLM)."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval_intent import (
    INTENTS,
    accuracy,
    confusion_matrix,
    evaluate,
    load_corpus,
    macro_f1,
    per_class,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evals" / "intent" / "corpus.csv"


def _rows(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"label": gold, "predicted": pred, "question": f"q{i}", "reason": "r", "confidence": "0.9"}
            for i, (gold, pred) in enumerate(pairs)]


# ------------------------------------------------------------------ métricas

def test_matriz_de_confusion_cuenta_pares():
    matrix = confusion_matrix(_rows(("dato", "dato"), ("dato", "dato"), ("navegar", "dato")))
    assert matrix[("dato", "dato")] == 2
    assert matrix[("navegar", "dato")] == 1
    assert matrix.get(("navegar", "navegar"), 0) == 0


def test_per_class_calcula_precision_recall_f1():
    # navegar: 2 aciertos, 1 falso positivo, 1 falso negativo
    rows = _rows(("navegar", "navegar"), ("navegar", "navegar"), ("navegar", "dato"), ("dato", "navegar"))
    data = per_class(confusion_matrix(rows))["navegar"]
    assert data["support"] == 3
    assert data["precision"] == pytest.approx(2 / 3, abs=1e-3)
    assert data["recall"] == pytest.approx(2 / 3, abs=1e-3)
    assert data["f1"] == pytest.approx(2 / 3, abs=1e-3)


def test_macro_f1_ignora_clases_ausentes():
    rows = _rows(("dato", "dato"), ("dato", "dato"))
    assert macro_f1(per_class(confusion_matrix(rows))) == 1.0


def test_accuracy_es_la_tasa_de_acierto():
    assert accuracy(_rows(("dato", "dato"), ("dato", "navegar"))) == 0.5


def test_evaluate_reporta_el_error_caro_de_navegar():
    """Un FP de `navegar` convierte una pregunta sustantiva en una lista de menciones."""
    rows = _rows(("analisis", "navegar"), ("navegar", "navegar"))
    report = evaluate(rows)
    assert report["critical"]["navegar"]["fp"] == 1
    assert report["critical"]["navegar"]["fp_rate"] == pytest.approx(0.5)


def test_evaluate_separa_las_preguntas_que_exigen_historial():
    rows = _rows(("dato", "dato"), ("navegar", "dato"))
    rows[1]["needs_history"] = "si"
    report = evaluate(rows)
    assert report["accuracy"] == 0.5
    assert report["no_history_cases"] == 1
    assert report["accuracy_no_history"] == 1.0


# ------------------------------------------------------------------- corpus

def test_corpus_semilla_existe_y_es_medible():
    rows, skipped = load_corpus(CORPUS)
    assert not skipped
    assert len(rows) >= 40
    for intent in INTENTS:
        support = sum(1 for r in rows if r["label"] == intent)
        assert support >= 5, f"el corpus necesita al menos 5 casos de {intent} (tiene {support})"


def test_corpus_no_tiene_preguntas_duplicadas():
    from app.services import text_terms

    rows, _ = load_corpus(CORPUS)
    keys = [text_terms.norm(r["question"]).strip() for r in rows]
    assert len(keys) == len(set(keys))


def test_corpus_trae_la_sugerencia_del_router_actual():
    """`suggested_label` debe estar rellena: es lo que hace rápida la etiqueta humana."""
    rows, _ = load_corpus(CORPUS)
    faltan = [r["question"] for r in rows if not r.get("suggested_label")]
    assert not faltan, f"sin sugerencia (corre --refresh): {faltan[:3]}"


def test_load_corpus_sin_etiquetas_falla(tmp_path):
    vacio = tmp_path / "corpus.csv"
    vacio.write_text('"question","label"\n"¿Qué dice la demanda?",\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        load_corpus(vacio)
