"""Confianza de OCR por página y por modo, ajustada por edición humana.

Modelo TRANSPARENTE (por CARACTERES de contenido):
- Toda página arranca en 100% de confianza para el modo que la procesó (`basico` o `document_ai`).
- Si la persona cambia contenido real, la confianza baja de forma **proporcional a los
  caracteres cambiados** (distancia de Levenshtein, donde una sustitución cuenta 1):

      confianza = base − (caracteres_cambiados / caracteres_totales)

  Así, cambiar UNA letra pesa menos que reescribir una palabra completa.
  Ejemplo: cambiar 1 letra en un texto de 100 caracteres → 100% − 1% = **99%**.
- Los cambios que sólo agregan/quitan **puntuación o espacios** NO afectan la confianza
  (se comparan solo letras/dígitos). Sin acentos ni mayúsculas se normalizan: se comparan
  tal cual.
"""
from __future__ import annotations

import re

from rapidfuzz.distance import Levenshtein

# "Palabra" = secuencia de letras/dígitos (con acentos). Ignora puntuación y espacios.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# Decimales de la confianza (coincide con numeric(6,5) en document_pages/document_ocr_versions).
_PRECISION = 5


def words(text: str | None) -> list[str]:
    """Lista de palabras (letras/dígitos) del texto, conservando mayúsculas y acentos."""
    return _WORD.findall(text or "")


def content_signature(text: str | None) -> str:
    """Texto sin puntuación ni espacios (solo letras/dígitos), para comparar contenido."""
    return "".join(words(text))


def _changed_chars(a: str, b: str) -> int:
    """Caracteres cambiados = distancia de Levenshtein (sustitución = 1, inserción/borrado = 1)."""
    return Levenshtein.distance(a, b)


def edit_metrics(old_text: str | None, new_text: str | None) -> dict[str, int]:
    """Métricas del cambio sobre el CONTENIDO: total de caracteres, cambiados y letras del nuevo."""
    a, b = content_signature(old_text), content_signature(new_text)
    total = max(len(a), len(b))
    changed = 0 if a == b else _changed_chars(a, b)
    return {"total": total, "changed": changed, "letters": len(b)}


def confidence_after_edit(old_confidence: float | None, old_text: str | None, new_text: str | None) -> float:
    """Confianza resultante tras una edición humana (0..1, `_PRECISION` decimales).

    - Sólo puntuación/espacios → conserva la confianza anterior.
    - Contenido → baja proporcionalmente a los caracteres cambiados (Levenshtein).
    """
    base = 1.0 if old_confidence is None else min(1.0, max(0.0, float(old_confidence)))
    a, b = content_signature(old_text), content_signature(new_text)
    if a == b:
        return round(base, _PRECISION)
    if not a:
        return 0.0
    total = max(len(a), len(b))
    drop = (_changed_chars(a, b) / total) if total else 0.0
    return round(max(0.0, base - drop), _PRECISION)
