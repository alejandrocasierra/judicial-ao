"""UT-CNF — confianza OCR ajustada por edición humana (por página y modo)."""
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
    """Cambiar letras reales baja la confianza (menos que 1, más que 0)."""
    c = oc.confidence_after_edit(1.0, "pagos", "pagas")
    assert 0.0 < c < 1.0


def test_ut_cnf_03_more_changes_lower_more():
    small = oc.confidence_after_edit(1.0, "abcdefghij", "abcdefghix")
    big = oc.confidence_after_edit(1.0, "abcdefghij", "xxxxxxxxxx")
    assert big < small


def test_ut_cnf_04_never_below_zero():
    assert oc.confidence_after_edit(0.1, "abc", "xyz") >= 0.0


def test_ut_cnf_05_empty_old_content():
    assert oc.confidence_after_edit(1.0, "", "texto nuevo") == 0.0
    assert oc.confidence_after_edit(1.0, " ... ", "   ") == 1.0  # sólo signos/espacios


def test_ut_cnf_06_content_signature_ignores_punctuation():
    assert oc.content_signature("N°19.169-590, ") == "N19169590"


def test_ut_cnf_07_min_drop_is_visible_on_long_pages():
    """Un cambio diminuto en una página larga igual baja al menos 1 punto."""
    old = ("palabra " * 500) + "final"
    new = ("palabra " * 500) + "finalx"
    assert oc.confidence_after_edit(1.0, old, new) <= 0.99
