"""Reconocimiento de líneas manuscritas (segunda pasada opcional).

RapidOCR (PP-OCR) reconoce bien el texto impreso pero no está entrenado para
manuscrito. Este módulo define un reconocedor de línea *image-to-text* (por ejemplo
TrOCR) que se aplica como **segunda pasada** a las líneas cuya confianza de RapidOCR
es baja. El reconocedor se inyecta (`LineRecognizer`) para poder probar la lógica sin
descargar pesos ni depender de `torch`.

`TrOCRLineRecognizer` requiere `transformers`, `torch` y `sentencepiece`. Si falta
alguna dependencia o el modelo no se puede cargar, `get_line_recognizer` devuelve
`None` y el pipeline continúa sólo con RapidOCR (degradación elegante).
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

import numpy as np

log = logging.getLogger(__name__)


class LineRecognizer(Protocol):
    """Reconoce el texto de una sola línea (imagen recortada)."""

    def recognize(self, image: np.ndarray) -> str: ...


def load_trocr_processor(model_id: str):
    """Carga un `TrOCRProcessor`, con fallback a construcción manual (ver clase de abajo)."""
    from transformers import TrOCRProcessor

    try:
        return TrOCRProcessor.from_pretrained(model_id)
    except Exception:  # noqa: BLE001 — fallback a construcción manual
        from transformers import ViTImageProcessor, XLMRobertaTokenizer

        tokenizer = XLMRobertaTokenizer.from_pretrained(model_id)
        image_processor = ViTImageProcessor.from_pretrained(model_id)
        return TrOCRProcessor(image_processor=image_processor, tokenizer=tokenizer)


class TrOCRLineRecognizer:
    """Reconocedor de manuscrito basado en TrOCR (transformers + torch)."""

    def __init__(self, model_id: str) -> None:
        from transformers import VisionEncoderDecoderModel

        self._processor = load_trocr_processor(model_id)
        self._model = VisionEncoderDecoderModel.from_pretrained(model_id)
        self._model.eval()

    def recognize(self, image: np.ndarray) -> str:
        import torch
        from PIL import Image

        pil = image if isinstance(image, Image.Image) else Image.fromarray(image)
        if pil.mode != "RGB":
            pil = pil.convert("RGB")
        pixel_values = self._processor(images=pil, return_tensors="pt").pixel_values
        with torch.no_grad():
            ids = self._model.generate(pixel_values, max_new_tokens=48)
        return self._processor.batch_decode(ids, skip_special_tokens=True)[0].strip()


def get_line_recognizer(model_id: str) -> LineRecognizer | None:
    """Carga el reconocedor de manuscritos; None si está deshabilitado o no disponible."""
    if not model_id:
        return None
    try:
        return TrOCRLineRecognizer(model_id)
    except Exception:  # noqa: BLE001 — dependencia o peso ausente: se degrada con aviso
        log.exception("no se pudo cargar el modelo de manuscritos %s; se usará OCR impreso", model_id)
        return None


def crop_box(image: np.ndarray, box: Any) -> np.ndarray | None:
    """Recorta la región de una caja (formato RapidOCR: ndarray/polígono de puntos)."""
    pts = box.tolist() if hasattr(box, "tolist") else box
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    h, w = image.shape[:2]
    x0, y0 = max(0, int(min(xs)) - 2), max(0, int(min(ys)) - 2)
    x1, y1 = min(w, int(max(xs)) + 2), min(h, int(max(ys)) + 2)
    if x1 <= x0 or y1 <= y0:
        return None
    return image[y0:y1, x0:x1]


def refine_lines(
    image: np.ndarray,
    boxes: list[Any],
    texts: list[str],
    scores: list[float],
    recognizer: LineRecognizer | None,
    threshold: float,
) -> tuple[list[str], list[float]]:
    """Segunda pasada: re-reconoce con `recognizer` las líneas de baja confianza.

    Sólo se reemplaza el texto (la confianza se conserva) para no enmascarar que la
    línea sigue siendo difícil y necesita revisión humana.
    """
    out_texts: list[str] = []
    out_scores: list[float] = []
    for box, text, score in zip(boxes, texts, scores, strict=True):
        score = float(score)
        if recognizer is not None and text.strip() and score < threshold:
            crop = crop_box(image, box)
            if crop is not None and crop.size:
                try:
                    handwriting = recognizer.recognize(crop)
                except Exception:  # noqa: BLE001 — una línea no debe tumbar la página
                    log.exception("falló el reconocimiento de manuscrito en una línea")
                    handwriting = ""
                if handwriting:
                    text = handwriting
        out_texts.append(text)
        out_scores.append(score)
    return out_texts, out_scores
