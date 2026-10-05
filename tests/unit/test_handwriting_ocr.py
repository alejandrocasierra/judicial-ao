"""UT-OCR-HW — segunda pasada de reconocimiento de manuscritos (sin descargar modelos)."""
from __future__ import annotations

import numpy as np
import pytest

from app.services.handwriting_ocr import crop_box, refine_lines

pytestmark = pytest.mark.unit


class _FakeRecognizer:
    """Reconocedor inyectado para probar la lógica sin cargar torch/TrOCR."""

    def __init__(self, mapping: dict[tuple[int, int], str]):
        self.mapping = mapping
        self.calls: list[tuple[int, int]] = []

    def recognize(self, image: np.ndarray) -> str:
        # Identifica la línea por su forma (alto x ancho) para no depender de los píxeles.
        key = image.shape[:2]
        self.calls.append(key)
        return self.mapping.get(key, "")


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_ut_hw_01_crop_box_recorta_y_limita_a_la_imagen():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    crop = crop_box(image, _box(10, 20, 50, 40))
    assert crop is not None and crop.shape[:2] == (24, 44)  # +2 px de margen por lado
    assert crop_box(image, _box(250, 10, 260, 20)) is None  # caja fuera de la imagen


def test_ut_hw_02_refina_solo_lineas_de_baja_confianza():
    image = np.arange(60 * 100 * 3, dtype=np.uint8).reshape(60, 100, 3)
    boxes = [_box(0, 0, 40, 20), _box(0, 30, 40, 50)]
    fake = _FakeRecognizer({(24, 42): "manuscrito"})
    texts, scores = refine_lines(
        image, boxes, texts=["impreso ok", "??????"], scores=[0.95, 0.40],
        recognizer=fake, threshold=0.85,
    )
    assert texts == ["impreso ok", "manuscrito"]
    # La confianza baja se conserva para que la página siga marcada como "revisar".
    assert scores == [0.95, 0.40]
    assert len(fake.calls) == 1  # sólo se llamó para la línea dudosa


def test_ut_hw_03_sin_reconocedor_no_cambia_nada():
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    boxes = [_box(0, 0, 40, 20)]
    texts, scores = refine_lines(image, boxes, ["texto"], [0.2], recognizer=None, threshold=0.85)
    assert texts == ["texto"] and scores == [0.2]


def test_ut_hw_04_reconocedor_que_falla_no_rompe_la_pagina():
    class _Boom:
        def recognize(self, image):
            raise RuntimeError("modelo caído")

    image = np.zeros((60, 100, 3), dtype=np.uint8)
    texts, scores = refine_lines(image, [_box(0, 0, 40, 20)], ["original"], [0.1], _Boom(), 0.85)
    assert texts == ["original"] and scores == [0.1]
