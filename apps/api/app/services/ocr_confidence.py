"""Confianza de OCR por página y por modo, ajustada por edición humana.

Regla de producto:
- Toda página arranca en 100% de confianza (para el modo que la procesó: `basico`
  o `document_ai`). Ese valor es la confianza "real" que se muestra.
- Si la persona corrige contenido real (palabra, letra, frase), la confianza baja de
  forma **proporcional** a lo cambiado.
- Los cambios que sólo agregan/quitan **puntuación o espacios** NO afectan la confianza.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# Sólo cuentan letras/dígitos (con acentos) para medir el cambio real.
_CONTENT = re.compile(r"[\W_]+", re.UNICODE)
# Cota de comparación: evita costos patológicos en páginas enormes (el ratio es aprox.).
_MAX_COMPARE_CHARS = 8000
# Intensidad mínima de la bajada: una edición real siempre se refleja (≥ 1 punto),
# aunque el cambio proporcional sea muy pequeño frente a una página larga.
_MIN_DROP = 0.01


def content_signature(text: str | None) -> str:
    """Texto sin puntuación ni espacios; conserva mayúsculas y acentos."""
    return _CONTENT.sub("", text or "")


def confidence_after_edit(old_confidence: float | None, old_text: str | None, new_text: str | None) -> float:
    """Confianza resultante tras una edición humana (0..1, 3 decimales).

    - Si sólo cambió puntuación/espacios, conserva la confianza anterior.
    - Si cambió contenido, baja proporcionalmente a la fracción cambiada, con un
      mínimo de `_MIN_DROP` para que la edición siempre sea visible."""
    base = 1.0 if old_confidence is None else min(1.0, max(0.0, float(old_confidence)))
    before = content_signature(old_text)[:_MAX_COMPARE_CHARS]
    after = content_signature(new_text)[:_MAX_COMPARE_CHARS]
    if before == after:
        return round(base, 3)
    if not before:
        return 0.0
    changed_ratio = 1.0 - SequenceMatcher(None, before, after, autojunk=False).ratio()
    return round(max(0.0, base - max(changed_ratio, _MIN_DROP)), 3)
