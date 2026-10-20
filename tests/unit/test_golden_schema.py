"""Esquema y balance del set dorado del pipeline de chat."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).resolve().parents[2] / "evals" / "golden" / "chat_pipeline.yaml"
REQUIRED = ("id", "question", "intent", "expected_answer_kind", "must_cite", "must_not_cite",
            "must_not_claim", "max_visible_citations", "latency_budget_ms", "notes")
INTENTS = {"dato", "navegar", "analisis", "procedimental", "agregacion", "lectura", "correccion"}
ANSWER_KINDS = {"direct", "partial", "insufficient", "navigational", "correction"}


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    data = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    return data["cases"]


def test_golden_tiene_entre_42_y_50_casos(cases):
    assert 42 <= len(cases) <= 50


def test_golden_campos_obligatorios(cases):
    for case in cases:
        for field in REQUIRED:
            assert field in case, f"{case.get('id')} no tiene {field}"
        assert isinstance(case["question"], str) and case["question"].strip()
        assert isinstance(case["must_cite"], list)
        assert isinstance(case["must_not_cite"], list)
        assert isinstance(case["must_not_claim"], list)
        assert isinstance(case["max_visible_citations"], int)
        assert isinstance(case["latency_budget_ms"], int) and case["latency_budget_ms"] > 0


def test_golden_ids_unicos_y_dominio_valido(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "ids duplicados"
    for case in cases:
        assert case["intent"] in INTENTS, case["id"]
        assert case["expected_answer_kind"] in ANSWER_KINDS, case["id"]


def test_golden_balance_por_intencion(cases):
    # El prefijo del id codifica el bloque del plan §2.3: dato, navegar, análisis,
    # procedimental, agregación, sin evidencia y adversariales.
    blocks = Counter(c["id"].split("-")[0] for c in cases)
    assert blocks == {"D": 12, "N": 8, "A": 8, "P": 6, "G": 6, "S": 5, "X": 5}

    counts = Counter(c["intent"] for c in cases)
    assert counts["navegar"] == 8
    assert counts["analisis"] >= 8
    assert counts["procedimental"] == 6
    assert counts["agregacion"] == 6
    assert counts["dato"] >= 12


def test_navegar_no_cita_y_declara_hechos_prohibidos(cases):
    for case in (c for c in cases if c["intent"] == "navegar"):
        assert case["expected_answer_kind"] == "navigational", case["id"]
        assert case["must_cite"] == [], case["id"]
        assert case["must_not_claim"], f"{case['id']} debe declarar hechos que no puede afirmar"
        assert case["latency_budget_ms"] <= 800, case["id"]


def test_abstencion_es_insufficient_y_sin_citas(cases):
    for case in (c for c in cases if c.get("expected_abstention")):
        assert case["expected_answer_kind"] == "insufficient", case["id"]
        assert case["must_cite"] == [], case["id"]
        assert case["max_visible_citations"] == 0, case["id"]


def test_cada_caso_tiene_criterio_de_evaluacion(cases):
    for case in cases:
        has_criteria = bool(case["must_cite"] or case["must_not_cite"] or case["must_not_claim"]
                            or case.get("must_contain") or case.get("expected_abstention"))
        assert has_criteria, f"{case['id']} no se puede evaluar (sin criterios)"
