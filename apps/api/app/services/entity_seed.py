"""Siembra de evidencia para preguntas sobre una persona o entidad.

«¿Quién es Jorge Humberto Rojas Melo?» no se responde con una búsqueda híbrida
genérica: el nombre puede estar en una página OCR o en un segmento ASR y el
retrieval por embedding local no lo prioriza. Se buscan menciones literales
(sin acentos) y se entregan como evidencia primaria al agente.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.engine import Connection

from app.core.db import rows
from app.services import text_terms

_NAME = re.compile(r"(?:qui[eé]n es|quien es|qui[eé]n fue|sobre)\s+(.+?)\s*\??$", re.I)


def _name(question: str) -> str:
    match = _NAME.search(question or "")
    if not match:
        return ""
    name = " ".join(text_terms.norm(match.group(1)).split())
    return name if 5 <= len(name) <= 80 else ""


def for_question(conn: Connection, case_id: str, question: str) -> list[dict[str, Any]]:
    name = _name(question)
    if not name:
        return []
    pattern = f"%{name}%"
    items: list[dict[str, Any]] = []

    pages = rows(conn, """
        SELECT dp.document_id, dp.page_number, dp.folio, d.filename, dp.text
          FROM document_pages dp
          JOIN documents d ON d.id = dp.document_id
         WHERE d.case_id = :c
           AND immutable_unaccent(lower(dp.text)) LIKE :p
         ORDER BY dp.page_number
         LIMIT 4
    """, c=case_id, p=pattern)
    for index, page in enumerate(pages, start=1):
        text = " ".join(str(page.get("text") or "").split())[:2000]
        if text:
            items.append({
                "handle": f"P{index}",
                "source_type": "document_page",
                "document_id": page.get("document_id"),
                "page_number": page.get("page_number"),
                "folio": page.get("folio"),
                "filename": page.get("filename"),
                "text": text,
                "seed": "entity",
            })

    segments = rows(conn, """
        SELECT s.id AS segment_id, s.media_id, s.start_ms, s.end_ms, s.text,
               m.filename, sp.display_name AS speaker
          FROM transcript_segments s
          JOIN media m ON m.id = s.media_id
          LEFT JOIN speakers sp ON sp.id = s.speaker_id
         WHERE m.case_id = :c
           AND immutable_unaccent(lower(s.text)) LIKE :p
         ORDER BY s.start_ms
         LIMIT 4
    """, c=case_id, p=pattern)
    for index, segment in enumerate(segments, start=1):
        text = " ".join(str(segment.get("text") or "").split())[:1200]
        if not text:
            continue
        payload = {
            "handle": f"T{index}",
            "source_type": "transcript_segment",
            "media_id": segment.get("media_id"),
            "segment_id": segment.get("segment_id"),
            "start_ms": segment.get("start_ms"),
            "end_ms": segment.get("end_ms"),
            "filename": segment.get("filename"),
            "speaker": segment.get("speaker"),
            "text": text,
            "seed": "entity",
        }
        if segment.get("start_ms") is not None:
            payload["start_mmss"] = text_terms.mmss(segment["start_ms"])
        items.append(payload)

    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = ((str(item.get("document_id")), item.get("page_number"))
               if item.get("document_id") else
               (str(item.get("media_id")), str(item.get("segment_id"))))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:6]


__all__ = ["for_question"]
