"""Segmentación estructural de documentos judiciales.

Antes de que el LLM extraiga actuaciones, el texto OCR de cada página se clasifica en
un segmento lógico:

- ``encabezado``: radicado, juzgado/tribunal, partes, fecha del documento.
- ``cuerpo``: antecedentes, hechos, considerandos, fundamentos.
- ``decision``: la parte resolutiva (RESUELVE / DECIDE / FALLA).
- ``anexos``: documentos anexos.

Saber DÓNDE está cada cosa mejora la extracción: la decisión (efecto procesal) vive en
el ``RESUELVE``, la autoridad y el radicado en el ``encabezado``, y las fechas narradas
en el ``cuerpo``. El segmento se envía al LLM como atributo de cada bloque de evidencia.
"""
from __future__ import annotations

import re
from typing import Any

SEGMENT_HEADER = "encabezado"
SEGMENT_BODY = "cuerpo"
SEGMENT_DECISION = "decision"
SEGMENT_ANNEXES = "anexos"

# Orden de precedencia al clasificar una página: lo resolutivo manda sobre el resto.
_DECISION_RE = re.compile(
    r"(?:^|\b)(?:R\s?E\s?S\s?U\s?E\s?L\s?V\s?[EO]|SE\s+RESUELVE|RESUELVE|RESUELVO|"
    r"DECIDE|DECIDO|FALLA|FALLO|DISPONE|DISPONGO|EN\s+M[EÉ]RITO\s+DE\s+LO\s+EXP(?:UESTO|RESADO))\b",
    re.IGNORECASE,
)
_ANNEX_RE = re.compile(
    r"(?:^|\b)(?:ANEXOS?|DOCUMENTO\s+ANEXO|ANEXO\s+N[o°]?\.?\s*\d|"
    r"PRUEBAS?\s+DOCUMENTALES?\s+ANEXAS?)\b",
    re.IGNORECASE,
)
_BODY_RE = re.compile(
    r"(?:^|\b)(?:ANTECEDENTES|HECHOS|CONSIDERANDOS?|CONSIDERACIONES|FUNDAMENTOS|"
    r"ARGUMENTOS?|FUNDAMENTACI[OÓ]N|MOTIVACI[OÓ]N|I\.\s*ANTECEDENTES|"
    r"PRIMERO|SEGUNDO|TERCERO)\b",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(
    r"(?:^|\b)(?:RADICACI[OÓ]N|RADICADO|JUZGADO|TRIBUNAL|REP[UÚ]BLICA\s+DE\s+COLOMBIA|"
    r"MAGISTRAD[OA]|PROCESO\s+N[o°]?|SE[ÑN]OR(?:A)?|DEMANDANTE|DEMANDADO|"
    r"APODERAD[OA]|SECRETARI[OA])\b",
    re.IGNORECASE,
)


def classify_text(text: str | None) -> str:
    """Clasifica un texto OCR en un segmento lógico.

    Precedencia: decision > anexos > cuerpo > encabezado > cuerpo (por defecto).
    """
    t = text or ""
    if _DECISION_RE.search(t):
        return SEGMENT_DECISION
    if _ANNEX_RE.search(t):
        return SEGMENT_ANNEXES
    if _BODY_RE.search(t):
        return SEGMENT_BODY
    if _HEADER_RE.search(t):
        return SEGMENT_HEADER
    return SEGMENT_BODY


def annotate_segments(items: list[dict[str, Any]]) -> int:
    """Anota cada item con su ``segment``. Devuelve cuántos se anotaron.

    Los items (páginas o segmentos de transcripción) deben traer ``text``. Las
    transcripciones de audiencia no tienen estructura documental: se marcan como
    ``cuerpo`` para no perder el atributo.
    """
    n = 0
    for it in items:
        if it.get("segment"):
            continue
        seg = classify_text(it.get("text"))
        it["segment"] = seg
        n += 1
    return n
