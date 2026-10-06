"""Confianza de OCR por página y por modo, ajustada por edición humana.

Modelo TRANSPARENTE (por palabras):
- Toda página arranca en 100% de confianza para el modo que la procesó (`basico` o `document_ai`).
- Si la persona cambia contenido real, la confianza baja de forma **proporcional a las
  palabras cambiadas**:

      confianza = base − (palabras_cambiadas / palabras_totales)

  Ejemplo: 100 palabras y se cambian 5 → 100% − 5% = **95%**.
- Los cambios que sólo agregan/quitan **puntuación o espacios** NO afectan la confianza
  (se comparan palabras: letras/dígitos).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# "Palabra" = secuencia de letras/dígitos (con acentos). Ignora puntuación y espacios.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def words(text: str | None) -> list[str]:
    """Lista de palabras (letras/dígitos) del texto, conservando mayúsculas y acentos."""
    return _WORD.findall(text or "")


def content_signature(text: str | None) -> str:
    """Texto sin puntuación ni espacios (letras/dígitos). Se conserva por compatibilidad."""
    return "".join(words(text))


def _changed_words(old: list[str], new: list[str]) -> int:
    """Palabras cambiadas = max(n_old, n_new) − coincidencias (LCS). Sustituciones cuentan 1."""
    sm = SequenceMatcher(None, old, new, autojunk=False)
    lcs = sum(size for _i, _j, size in sm.get_matching_blocks())
    return max(len(old), len(new)) - lcs


def edit_metrics(old_text: str | None, new_text: str | None) -> dict[str, int]:
    """Métricas del cambio: total de palabras, cambiadas, y letras del texto nuevo."""
    old, new = words(old_text), words(new_text)
    total = max(len(old), len(new))
    changed = 0 if old == new else _changed_words(old, new)
    return {"total": total, "changed": changed, "letters": len(content_signature(new_text))}


def confidence_after_edit(old_confidence: float | None, old_text: str | None, new_text: str | None) -> float:
    """Confianza resultante tras una edición humana (0..1, 3 decimales).

    - Sólo puntuación/espacios → conserva la confianza anterior.
    - Contenido → baja proporcionalmente a las palabras cambiadas.
    """
    base = 1.0 if old_confidence is None else min(1.0, max(0.0, float(old_confidence)))
    old, new = words(old_text), words(new_text)
    if old == new:
        return round(base, 3)
    if not old:
        return 0.0
    total = max(len(old), len(new))
    drop = (_changed_words(old, new) / total) if total else 0.0
    return round(max(0.0, base - drop), 3)
