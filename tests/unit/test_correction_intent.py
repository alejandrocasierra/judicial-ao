"""UT-COR — detección de intención de corrección (Fase 6).

El usuario no escribe "esto está malo, es así": estas pruebas fijan el
comportamiento con lenguaje natural variado.
"""
from __future__ import annotations

import pytest

from app.services import correction

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("text", ["sí", "si", "Sí, confirma", "dale", "adelante", "confirma", "aplícalo",
                                  "ok", "de acuerdo", "hazlo", "correcto", "así es", "sí."])
def test_ut_cor_01_confirmation(text):
    assert correction.is_confirmation(text)


@pytest.mark.parametrize("text", ["no", "No.", "cancela", "cáncelalo", "déjalo", "déjalo así",
                                  "olvídalo", "mejor no", "no, cancela", "descártalo"])
def test_ut_cor_02_cancellation(text):
    assert correction.is_cancellation(text)


@pytest.mark.parametrize("text", ["sí, ese es el nombre correcto", "no estoy seguro de eso", "no lo sé"])
def test_ut_cor_03_not_confirmation_or_cancellation(text):
    assert not correction.is_confirmation(text) and not correction.is_cancellation(text)


@pytest.mark.parametrize("text", [
    "el OCR dice «Rueda» pero en realidad es «Rueda Gómez»",
    "eso no dice lo que pusiste",
    "está mal transcrito el minuto 3",
    "ahí hay un error, cambia el nombre",
    "corrige la página 12",
    "falta una palabra en la página 4",
    "el hablante de ese segmento no es él",
    "quise decir «demandado», no «demandante»",
    "confundió los apellidos",
    "donde dice ‘Bogotá’ debería decir ‘Medellín’",
    "no es ese nombre",
])
def test_ut_cor_04_correction_intent(text):
    assert correction.is_correction(text), text


@pytest.mark.parametrize("text", ["¿cómo podemos mejorar este OCR?",
                                  "este documento tiene baja calidad, hay forma de mejorarlo",
                                  "se puede reprocesar con el otro motor",
                                  "el OCR de este expediente es poco legible, ideas?",
                                  "podemos mejorar la transcripción del video"])
def test_ut_cor_05_reprocess_question(text):
    assert correction.is_reprocess_question(text), text


def test_ut_cor_06_hint_prefers_correction_over_reprocess():
    h = correction.hint("el OCR está mal, en realidad dice otra cosa", has_attachments=True)
    assert h and "CORRIGIENDO" in h and "confirm" in h.lower()
    assert "adjuntó" in h
    h2 = correction.hint("¿cómo mejoramos el OCR?", has_attachments=False)
    assert h2 and "suggest_reprocess" in h2


def test_ut_cor_07_plain_question_has_no_hint():
    assert correction.hint("¿quién presentó la demanda?", has_attachments=False) is None
    assert not correction.is_correction("¿cuánto se pagó según el contrato?")
