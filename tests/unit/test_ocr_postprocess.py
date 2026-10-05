"""UT-OCRP — post-proceso del texto OCR (limpieza de formularios)."""
from __future__ import annotations

import pytest

from app.services.ocr_postprocess import (
    clean_form_artifacts,
    normalize_ocr_text,
    strip_garbage_chars,
)

pytestmark = pytest.mark.unit


def test_ut_ocrp_01_quita_guiones_bajos_pegados_a_etiquetas():
    assert normalize_ocr_text("Folios Correspondientes en original:____") == \
        "Folios Correspondientes en original:"
    assert normalize_ocr_text("Dirección Notificación_") == "Dirección Notificación"


def test_ut_ocrp_02_elimina_lineas_de_regla_del_formulario():
    assert normalize_ocr_text("uno\n______\ndos") == "uno\ndos"
    assert normalize_ocr_text("uno\n- - - - -\ndos") == "uno\ndos"
    assert normalize_ocr_text("uno\n_._._._\ndos") == "uno\ndos"


def test_ut_ocrp_03_conserva_el_texto_util_entre_reglas():
    # Un número dentro de dos reglas debe sobrevivir.
    assert normalize_ocr_text("______ 201800361 ______") == "201800361"


def test_ut_ocrp_04_elimina_glifos_basura():
    assert strip_garbage_chars("abc鑫def") == "abcdef"
    assert strip_garbage_chars("texto\ufffdcon reemplazo") == "textocon reemplazo"
    # Las tildes y signos latinos legítimos se conservan.
    assert strip_garbage_chars("N° 1ª Apellido — JURISDICCIÓN") == "N° 1ª Apellido — JURISDICCIÓN"


def test_ut_ocrp_05_normaliza_espacios_y_lineas_en_blanco():
    assert normalize_ocr_text("a   \nb") == "a\nb"
    assert normalize_ocr_text("a\n\n\n\nb") == "a\n\nb"
    assert normalize_ocr_text("  hola  ") == "hola"


def test_ut_ocrp_06_texto_vacio_o_nulo():
    assert normalize_ocr_text("") == ""
    assert normalize_ocr_text(None) == ""


def test_ut_ocrp_08_marcas_de_casilla_se_vuelven_texto():
    # Una casilla marcada "_X_" no debe conservar guiones bajos.
    assert normalize_ocr_text("comparecer de inmediato _X_ dentro") == "comparecer de inmediato X dentro"
    assert normalize_ocr_text("inmediato _X_o dentro de los _X_ 5 días") == "inmediato X o dentro de los X 5 días"


def test_ut_ocrp_09_conserva_alineacion_interna_y_quita_espacios_finales():
    # Los huecos internos de columna se conservan; los espacios finales no.
    assert normalize_ocr_text("Nombre(s):Vose   1ª Apellido") == "Nombre(s):Vose   1ª Apellido"
    assert normalize_ocr_text("DEMANDANTE(S)      ") == "DEMANDANTE(S)"


def test_ut_ocrp_07_clean_form_artifacts_no_toca_contenido_real():
    assert clean_form_artifacts("JURISDICCIÓN:Civil Del Circuito De Bogotá") == \
        "JURISDICCIÓN:Civil Del Circuito De Bogotá"
