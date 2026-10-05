"""Detección de intención de corrección del chat (Fase 6).

El usuario NO escribe "esto está malo, es así": dice cosas como "no, ahí dice
otra cosa", "el OCR confundió el nombre", "falta una palabra", "en el minuto 3
el hablante no es ese", "¿cómo mejoramos este OCR?". Este módulo reconoce esa
intención de forma DETERMINISTA (para no depender de que el LLM la capte) y:

- `hint()`: instrucción extra para el agente cuando huele a corrección o a
  pregunta de calidad (el agente aplica la corrección directamente).
- `is_confirmation()` / `is_cancellation()`: respuestas cortas unívocas
  ("sí", "dale", "confirma" / "no", "cancela", "déjalo") que el servidor
  aplica/descarta sobre la corrección pendiente sin depender del LLM.
"""
from __future__ import annotations

import re

# Confirmaciones cortas y unívocas (se evalúan sobre el mensaje completo).
_CONFIRM = re.compile(
    r"^\s*(?:s[íi]|s[íi]\s*(?:,|\.)?\s*(?:confirma|aplica|apl[íi]calo|dale|adelante|hazlo)?|"
    r"dale|confirmo|confirmar|confirma|aplicar|aplica|apl[íi]calo|aplicalo|correcto|ok|okay|"
    r"de acuerdo|adelante|hazlo|procede|proceda|as[íi] es)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Cancelaciones cortas ("no", "no, déjalo", "cancela", "mejor no", "olvídalo").
_CANCEL = re.compile(
    r"^\s*(?:no|cancelar|cancela|c[áa]ncelalo|d[ée]jalo(?:\s+as[íi])?|olv[íi]dalo|olvida|"
    r"descartar|desc[áa]rtalo|mejor\s+no|no\s*,?\s*(?:d[ée]jalo|cancela|olv[íi]dalo|as[íi]))"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)

_REPROCESS = re.compile(
    r"\breproces\w*|\botro\s+motor\b|"
    r"(?:mejorar|mejora|calidad|confianza|legible|ilegible|poco\s+claro|poca\s+calidad)"
    r"[^.]{0,60}(?:ocr|asr|transcrip\w*|texto|documento|video|video)|"
    r"(?:ocr|asr|transcrip\w*|documento|video|video)[^.]{0,60}"
    r"(?:mejorar|mejora|reproces\w*|calidad|confianza|legible|ilegible)",
    re.IGNORECASE,
)

# Señales de que el usuario está corrigiendo un texto/timestamp concreto.
_CORRECTION = [
    re.compile(p, re.IGNORECASE) for p in (
        r"en\s+realidad", r"no\s+dice", r"no\s+es\s+lo\s+que\s+dice", r"est[áa]\s+mal", r"estaba\s+mal",
        r"mal\s+(?:transcrit|escrit|ocr|asr|puesto|le[íi]do)", r"corrig", r"correcci[óo]n", r"corregir",
        r"err[óo]ne[oa]", r"equivoc\w*", r"\berror\b", r"deber[íi]a\s+decir", r"tendr[íi]a\s+que\s+decir",
        r"donde\s+dice", r"dice\s+[^.]{0,40}(?:pero|sino|y\s+es|no\s+es)", r"no\s+es\s+[^.]{0,25}\s+sino",
        r"\bfalta\b", r"\bsobra\b", r"confundi\w*", r"c[áa]mbial\w*", r"reemplaz\w*", r"sustituy\w*",
        r"as[íi]\s+dice", r"otra\s+palabra", r"otra\s+frase", r"quise\s+decir", r"quer[íi]a\s+decir",
        r"no\s+es\s+ese\s+(?:nombre|dato|hablante|minuto)", r"el\s+hablante\s+no", r"no\s+lo\s+dijo",
        r"hablante[^.]{0,40}no\b",
        r"quien\s+dijo", r"no\s+era\s+(?:ese|esa|ahi|ah[íi])",
    )
]


def is_confirmation(text: str) -> bool:
    return bool(_CONFIRM.match(text or ""))


def is_cancellation(text: str) -> bool:
    return bool(_CANCEL.match(text or ""))


def is_correction(text: str) -> bool:
    t = text or ""
    # Una confirmación/cancelación corta no es una corrección nueva.
    if is_confirmation(t) or is_cancellation(t):
        return False
    return any(rx.search(t) for rx in _CORRECTION)


def is_reprocess_question(text: str) -> bool:
    return bool(_REPROCESS.search(text or ""))


def hint(text: str, has_attachments: bool) -> str | None:
    """Instrucción que se inyecta al agente cuando el mensaje huele a corrección
    o a pregunta de calidad. None si no aplica."""
    if is_correction(text):
        base = (
            "El usuario está CORRIGIENDO contenido del expediente (OCR o transcripción). "
            "Identifica con precisión QUÉ se corrige (documento+página, o media+segmento/minuto) y el "
            "texto correcto que indica el usuario. APLICA la corrección directamente en este mismo turno: "
            "llama la herramienta de corrección con confirm=true y NO le pidas confirmación al usuario. "
            "Si la corrección es el NOMBRE de un hablante, usa list_speakers y rename_speaker con confirm=true. "
            "Si no sabes a qué página/segmento se refiere, pregúntalo (puedes buscar el texto actual con "
            "search_case). Al terminar, informa brevemente el resultado."
        )
        if has_attachments:
            base += " El usuario adjuntó un archivo con @: úsalo para ubicar la parte a corregir."
        return base
    if is_reprocess_question(text):
        return (
            "El usuario pregunta cómo MEJORAR la calidad del OCR/ASR. Usa suggest_reprocess para "
            "diagnosticar (confianza, páginas/segmentos para revisión, motores disponibles) y proponer "
            "reprocesar con el otro motor o corregir páginas puntuales. No apliques el reproceso sin "
            "confirmación."
        )
    return None
