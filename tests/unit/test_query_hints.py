"""UT-HINT — recordatorio general de búsqueda (todas las fuentes), sin reglas por palabra."""
from __future__ import annotations

import pytest

from app.services import query_hints

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("text", [
    "¿Qué me puedes decir del pagaré 001?",
    "¿En qué minuto habló la doctora Paola?",
    "¿Quién es Jorge Humberto Rojas Melo?",
    "resume el expediente",
    "",
])
def test_ut_hint_01_always_general_all_sources(text):
    h = query_hints.hint(text)
    assert h
    low = h.lower()
    assert "documentos" in low and "transcrip" in low and "grafo" in low
    assert "minuto" in low and "página" in low


def test_ut_hint_02_merge_drops_empty():
    assert query_hints.merge(None, None) is None
    assert query_hints.merge(None, "x") == ["x"]
    assert query_hints.merge("a", "b") == ["a", "b"]


@pytest.mark.parametrize("text,expected", [
    ("¿Dónde se habla del pagaré 001?", "pagaré 001"),
    ("¿En qué archivos aparece el pagaré 001?", "pagaré 001"),
    ("¿en qué página se menciona el pagaré 001?", "pagaré 001"),
])
def test_ut_hint_03_locate_intent(text, expected):
    assert query_hints.locate_term(text) == expected


@pytest.mark.parametrize("text", [
    "¿Qué me puedes decir del pagaré 001?",
    "¿En qué minuto habló la doctora Paola?",
    "¿Quién es Jorge Humberto Rojas Melo?",
    "resume el expediente",
])
def test_ut_hint_04_not_locate(text):
    assert query_hints.locate_term(text) is None


@pytest.mark.parametrize("text", [
    "¿cuántos jueces han intervenido en el proceso?",
    "¿quiénes son los apoderados?",
    "cuántos testigos intervinieron",
])
def test_ut_hint_05_role_aggregation_points_to_tool(text):
    assert "list_people_by_role" in query_hints.hint(text)


@pytest.mark.parametrize("text", [
    "¿En qué minuto habló la doctora Paola?",
    "resume el expediente",
])
def test_ut_hint_06_other_questions_do_not_get_role_hint(text):
    assert "list_people_by_role" not in query_hints.hint(text)
