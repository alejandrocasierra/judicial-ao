"""UT-CNF — confianza OCR ajustada por edición humana (por página y modo).

Modelo: confianza = base − (palabras_cambiadas / palabras_totales).
"""
import pytest

from app.services import ocr_confidence as oc

pytestmark = pytest.mark.unit


def test_ut_cnf_01_punctuation_and_spaces_do_not_lower_confidence():
    """Agregar/quitar punto, coma o espacios no afecta la confianza."""
    assert oc.confidence_after_edit(1.0, "Hola mundo, señor.", "Hola mundo señor") == 1.0
    assert oc.confidence_after_edit(1.0, "pago", "pago.") == 1.0
    assert oc.confidence_after_edit(1.0, "a b", "a  b") == 1.0
    assert oc.confidence_after_edit(None, "texto", "texto,") == 1.0


def test_ut_cnf_02_real_edit_lowers_proportionally():
    """4 palabras, 1 cambiada → cae 25% (0.75)."""
    assert oc.confidence_after_edit(1.0, "el pago es alto", "el pago es bajo") == 0.75
    c = oc.confidence_after_edit(1.0, "pagos", "pagas")
    assert 0.0 <= c < 1.0


def test_ut_cnf_03_more_changes_lower_more():
    small = oc.confidence_after_edit(1.0, "el juez ordeno pagar", "el juez ordeno pagas")
    big = oc.confidence_after_edit(1.0, "el juez ordeno pagar", "xxx xxx xxx xxx")
    assert big < small


def test_ut_cnf_04_never_below_zero():
    assert oc.confidence_after_edit(0.1, "abc", "xyz") >= 0.0


def test_ut_cnf_05_empty_old_content():
    assert oc.confidence_after_edit(1.0, "", "texto nuevo") == 0.0
    assert oc.confidence_after_edit(1.0, " ... ", "   ") == 1.0  # sólo signos/espacios


def test_ut_cnf_06_content_signature_ignores_punctuation():
    assert oc.content_signature("N°19.169-590, ") == "N19169590"
    assert oc.words("N°19.169-590, hola") == ["N", "19", "169", "590", "hola"]


def test_ut_cnf_07_proportional_formula_example():
    """100 palabras, 5 cambiadas → 95% (el ejemplo de producto)."""
    old = " ".join(f"p{i}" for i in range(100))
    new = " ".join(("cambio" if i < 5 else f"p{i}") for i in range(100))
    assert oc.confidence_after_edit(1.0, old, new) == 0.95
    m = oc.edit_metrics(old, new)
    assert m["total"] == 100 and m["changed"] == 5


def test_ut_cnf_08_edit_metrics_counts_words_and_letters():
    m = oc.edit_metrics("hola mundo", "hola mundo cruel")
    assert m["total"] == 3 and m["changed"] == 1 and m["letters"] == 14
    assert oc.edit_metrics("abc", "abc") == {"total": 1, "changed": 0, "letters": 3}
