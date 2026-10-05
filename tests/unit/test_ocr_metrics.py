"""UT-OCRM — métricas de OCR (CER/WER)."""
from __future__ import annotations

import pytest

from app.services.ocr_metrics import cer, edit_distance, normalize_text, wer

pytestmark = pytest.mark.unit


def test_ut_ocrm_01_cer_sustitucion_y_longitud():
    assert cer("abc", "abc") == 0.0
    assert cer("abc", "abd") == pytest.approx(1 / 3)
    assert cer("abc", "abcd") == pytest.approx(1 / 3)
    assert cer("abc", "ab") == pytest.approx(1 / 3)


def test_ut_ocrm_02_cer_referencia_vacia():
    assert cer("", "") == 0.0
    assert cer("", "algo") == 1.0


def test_ut_ocrm_03_normaliza_acentos_y_caja():
    assert cer("José Ángel", "jose angel") == 0.0
    assert normalize_text("  Hölá   MUNDO ") == "hola mundo"


def test_ut_ocrm_04_wer_palabras():
    assert wer("hola mundo", "hola mundo") == 0.0
    assert wer("hola mundo", "hola") == pytest.approx(0.5)
    assert wer("hola mundo", "adios mundo") == pytest.approx(0.5)


def test_ut_ocrm_05_edit_distance():
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "abc") == 3
