"""Etiquetas cortas de fuentes para las etapas del chat.

La UI necesita decir «leyendo 0067... p. 2», no «consultando search_case».
"""
from __future__ import annotations

from typing import Any

from app.services import text_terms


def one(item: dict[str, Any]) -> str:
    source = str(item.get("source_type") or "")
    if source == "transcript_segment" or item.get("segment_id"):
        name = str(item.get("filename") or item.get("title") or "audio/video")
        start = item.get("start_ms")
        minute = item.get("start_mmss") or (text_terms.mmss(start) if start is not None else "")
        speaker = str(item.get("speaker") or "").strip()
        bits = [name]
        if minute:
            bits.append(f"min. {minute}")
        if speaker:
            bits.append(speaker)
        return " · ".join(bits)
    name = str(item.get("filename") or "documento")
    page = item.get("page_number") or item.get("page")
    folio = item.get("folio")
    if page is not None:
        name += f" · p. {page}"
    if folio and str(folio) != str(page):
        name += f" (folio {folio})"
    return name


def many(items: list[dict[str, Any]] | None, limit: int = 3) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        label = one(item)
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
        if len(out) >= limit:
            break
    return out


__all__ = ["one", "many"]
