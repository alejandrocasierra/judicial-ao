"""UT-CNF — confianza OCR ajustada por edición humana (por página y modo).

Modelo: confianza = base − (caracteres_cambiados / caracteres_totales), con la
distancia de Levenshtein sobre el CONTENIDO (letras/dígitos; sin puntuación ni
espacios). Así, cambiar una letra pesa menos que reescribir una palabra completa.
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


def test_ut_cnf_02_single_letter_weighs_less_than_full_word():
    """Granularidad por letra: 1 letra baja menos que reescribir la palabra."""
    letra = oc.confidence_after_edit(1.0, "el juez ordeno pagar", "el juez ordeno pahar")
    palabra = oc.confidence_after_edit(1.0, "el juez ordeno pagar", "el juez ordeno dictar")
    assert palabra < letra < 1.0


def test_ut_cnf_03_proportional_to_characters():
    """10 caracteres de contenido, 1 cambiado → 0.9."""
    assert oc.confidence_after_edit(1.0, "abcdefghij", "abcdefghiX") == 0.9
    # 4 palabras (12 caracteres), 1 palabra distinta (3 letras) → cae 25%
    assert oc.confidence_after_edit(1.0, "el pago es alto", "el pago es bajo") == 0.75


def test_ut_cnf_04_never_below_zero():
    assert oc.confidence_after_edit(0.1, "abc", "xyz") == 0.0
    assert oc.confidence_after_edit(0.5, "abc", "xyz") >= 0.0


def test_ut_cnf_05_empty_old_content():
    assert oc.confidence_after_edit(1.0, "", "texto nuevo") == 0.0
    assert oc.confidence_after_edit(1.0, " ... ", "   ") == 1.0  # sólo signos/espacios


def test_ut_cnf_06_content_signature_ignores_punctuation():
    assert oc.content_signature("N°19.169-590, ") == "N19169590"
    assert oc.words("N°19.169-590, hola") == ["N", "19", "169", "590", "hola"]


def test_ut_cnf_07_edit_metrics_counts_characters():
    assert oc.edit_metrics("hola mundo", "hola mundo cruel") == {"total": 14, "changed": 5, "letters": 14}
    assert oc.edit_metrics("abc", "abc") == {"total": 3, "changed": 0, "letters": 3}


def test_ut_cnf_08_confidence_keeps_five_decimals():
    """La nueva precisión (numeric(6,5)) conserva bajadas pequeñas por letra."""
    assert oc.confidence_after_edit(1.0, "pagos", "pagas") == 0.8
    c = oc.confidence_after_edit(1.0, "x" * 1000, "x" * 999 + "y")  # 1 letra en 1000
    assert c == round(1 - 1 / 1000, 5) == 0.999
