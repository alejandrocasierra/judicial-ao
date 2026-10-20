"""Ubicaciones de un término (contrato §1.7).

`occurrences` deja de ser un cálculo universal: solo se ejecuta cuando el
usuario pide una ubicación (intención `navegar` o señal explícita) y nunca
acompaña a una respuesta de insuficiencia o de corrección.

El resultado se agrupa por archivo, se deduplica por minuto/hablante (o página)
y se fusionan segmentos contiguos del mismo hablante para no repetir la misma
intervención troceada por el ASR.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from app.core.config import get_settings
from app.core.i18n import t
from app.services import text_terms

_EXPLICIT_LOCATION = re.compile(
    r"\bdonde\b[^?]{0,60}\b(aparece|aparecen|se habla|se menciona|se dice|se trata|"
    r"se registra|consta|figura|figuran)\b"
    r"|\ben que (minuto|minutos|pagina|paginas|archivo|archivos|folio|folios|parte|partes|"
    r"seccion|momento|timestamp|video|audio|grabacion)\b"
    r"|\ba que (minuto|hora|pagina)\b"
    r"|\ben cual (pagina|minuto|archivo|parte|folio)\b"
    r"|\b(localiza|localizar|localizame|ubica|ubicame|ubicacion|timestamp)\b"
    r"|\bbusca (la palabra|el termino|la frase)\b",
    re.IGNORECASE,
)

_MERGE_GAP_MS = 2000


def should_locate(decision: Any, answer_kind: str, question: str) -> bool:
    """¿Procede calcular ubicaciones para este turno?"""
    if answer_kind in ("insufficient", "correction"):
        return False
    intent = str(getattr(decision, "intent", None) or "")
    intent_value = str(getattr(getattr(decision, "intent", None), "value", intent) or "")
    if intent_value == "navegar":
        return True
    return bool(_EXPLICIT_LOCATION.search(text_terms.norm(question or "")))


def _term_is_informative(term: str) -> bool:
    folded = text_terms.norm(term).strip()
    if not folded:
        return False
    if folded in text_terms.STOPWORDS or folded in text_terms.REPORT_WORDS:
        return False
    compact = re.sub(r"[^0-9a-z]", "", folded)
    if compact.isdigit():
        return len(compact) >= 2
    return len(compact) >= 3 and bool(text_terms.content_terms(term))


def is_informative(term: str | None) -> bool:
    """¿Merece la pena buscar este término con `locate`?"""
    return _term_is_informative(term or "")


def _minute(start_ms: Any) -> int | None:
    try:
        return int(start_ms) // 60000
    except (TypeError, ValueError):
        return None


def _is_media(item: dict) -> bool:
    return str(item.get("source_type") or "") == "transcript_segment" or bool(item.get("media_id"))


def _normalize(locs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in locs or []:
        if not isinstance(item, dict):
            continue
        if item.get("source_type") == "error":
            continue
        candidate = dict(item)
        candidate["_media"] = _is_media(candidate)
        if candidate["_media"] and candidate.get("start_mmss") in (None, ""):
            candidate["start_mmss"] = text_terms.mmss(candidate.get("start_ms"))
        out.append(candidate)
    return out


def _merge_contiguous(items: list[dict]) -> list[dict]:
    """Fusiona segmentos consecutivos del mismo hablante/archivo (gap ≤ 2 s)."""
    merged: list[dict] = []
    for item in sorted(items, key=lambda i: (str(i.get("filename") or ""), int(i.get("start_ms") or 0))):
        previous = merged[-1] if merged else None
        same = (
            previous is not None
            and previous.get("_media") and item.get("_media")
            and str(previous.get("filename")) == str(item.get("filename"))
            and (previous.get("speaker") or "") == (item.get("speaker") or "")
            and previous.get("start_ms") is not None and item.get("start_ms") is not None
            and 0 <= int(item["start_ms"]) - int(previous.get("end_ms") or previous["start_ms"]) <= _MERGE_GAP_MS
        )
        if same:
            previous["end_ms"] = max(int(previous.get("end_ms") or 0), int(item.get("end_ms") or item["start_ms"]))
            previous["end_mmss"] = text_terms.mmss(previous["end_ms"])
            if len(str(item.get("text") or "")) > len(str(previous.get("text") or "")):
                previous["text"] = item.get("text")
            continue
        merged.append(item)
    return merged


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for item in items:
        if item.get("_media"):
            key = (str(item.get("filename")), str(_minute(item.get("start_ms"))),
                   str(item.get("speaker") or ""))
        else:
            key = (str(item.get("filename")), str(item.get("page_number")))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _citation(item: dict, marker: int) -> dict:
    base = {
        "citation_id": f"loc{marker}",
        "marker": marker,
        "group": item.get("filename"),
        "source_type": "transcript_segment" if item.get("_media") else "document_page",
        "filename": item.get("filename"),
    }
    if item.get("_media"):
        base.update({"media_id": item.get("media_id"), "start_ms": item.get("start_ms"),
                     "end_ms": item.get("end_ms"), "speaker": item.get("speaker"),
                     "start_mmss": item.get("start_mmss")})
    else:
        base.update({"document_id": item.get("document_id"), "page": item.get("page_number"),
                     "folio": item.get("folio")})
    if item.get("ocr_mode") is not None:
        base["ocr_mode"] = item.get("ocr_mode")
    if item.get("asr_mode") is not None:
        base["asr_mode"] = item.get("asr_mode")
    return base


def _line(item: dict, marker: int, locale: str) -> str:
    if item.get("_media"):
        minute = item.get("start_mmss") or text_terms.mmss(item.get("start_ms"))
        speaker = f" · {item['speaker']}" if item.get("speaker") else ""
        return f"- {minute}{speaker} [{marker}]"
    return f"- {t('messages.occurrences_page', locale).format(page=item.get('page_number'))} [{marker}]"


def build(term: str, locs: list[dict], locale: str,
          max_items: int | None = None) -> tuple[str, list[dict]]:
    """Devuelve (texto Markdown con la nota obligatoria, citas agrupadas)."""
    term = (term or "").strip().strip("«»\"'")
    if not _term_is_informative(term):
        return t("messages.occurrences_none", locale).format(term=term or "—"), []

    limit = max_items or get_settings().NAV_MAX_OCCURRENCES
    items = _dedupe(_merge_contiguous(_normalize(locs)))
    if not items:
        return t("messages.occurrences_none", locale).format(term=term), []

    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for item in items:
        groups.setdefault(str(item.get("filename") or "?"), []).append(item)

    marker = 0
    cites: list[dict] = []
    blocks: list[str] = []
    for filename, group in groups.items():
        group = sorted(group, key=lambda i: (int(i.get("start_ms") or 0), int(i.get("page_number") or 0)))
        lines: list[str] = []
        for item in group:
            if marker >= limit:
                break
            marker += 1
            lines.append(_line(item, marker, locale))
            cites.append(_citation(item, marker))
        if lines:
            header = t("messages.occurrences_group", locale).format(filename=filename, count=len(lines))
            blocks.append(header + "\n" + "\n".join(lines))
        if marker >= limit:
            break

    note = t("messages.occurrences_note", locale).format(term=term)
    return note + "\n\n" + "\n\n".join(blocks), cites
