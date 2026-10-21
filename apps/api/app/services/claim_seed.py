"""Siembra de la fuente de un hecho concreto del banco de claims.

Las preguntas de tipo `claim` incluyen el texto de un claim ya extraído. Si la
búsqueda híbrida no recupera su página, el modelo responde «no hay evidencia»
aunque el hecho esté en el expediente. Aquí se resuelve el claim en `claims` y
se inyectan sus páginas/segmentos citados como evidencia primaria.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.engine import Connection

from app.core.db import rows
from app.services import contradiction_seed, text_terms


def _match(conn: Connection, case_id: str, question: str) -> dict[str, Any] | None:
    tail = question.split("respalda?", 1)[-1].strip()
    if len(tail) < 30:
        return None
    target = text_terms.norm(tail)[:100]
    if not target:
        return None
    found = rows(conn, """
        SELECT id::text, text
          FROM claims
         WHERE case_id = :c
           AND position(:target in immutable_unaccent(lower(text))) > 0
         LIMIT 5
    """, c=case_id, target=target)
    if found:
        return found[0]
    # El claim puede venir truncado o con una paráfrasis mínima: comparar tokens.
    wanted = set(text_terms.content_terms(tail))
    if not wanted:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for claim in rows(conn, "SELECT id::text, text FROM claims WHERE case_id = :c LIMIT 5000",
                      c=case_id):
        terms = set(text_terms.content_terms(claim.get("text")))
        if not terms:
            continue
        overlap = len(wanted & terms) / len(wanted)
        if overlap >= 0.8 and (best is None or overlap > best[0]):
            best = (overlap, claim)
    return best[1] if best else None


def _literal_items(conn: Connection, case_id: str, text: str,
                   start: int = 0) -> list[dict[str, Any]]:
    """Si el claim no tiene citas, buscar su texto literal en páginas/segmentos."""
    probe = text_terms.norm(text)[:80]
    if len(probe) < 30:
        return []
    items: list[dict[str, Any]] = []
    pages = rows(conn, """
        SELECT dp.document_id, dp.page_number, dp.folio, d.filename, dp.text
          FROM document_pages dp
          JOIN documents d ON d.id = dp.document_id
         WHERE d.case_id = :c
           AND position(:p in immutable_unaccent(lower(dp.text))) > 0
         ORDER BY dp.page_number LIMIT 3
    """, c=case_id, p=probe)
    if not pages:
        for number in sorted(set(re.findall(r"\d[\d.,]{5,}", text)), key=len, reverse=True)[:2]:
            pages = rows(conn, """
                SELECT dp.document_id, dp.page_number, dp.folio, d.filename, dp.text
                  FROM document_pages dp
                  JOIN documents d ON d.id = dp.document_id
                 WHERE d.case_id = :c AND position(:p in dp.text) > 0
                 ORDER BY dp.page_number LIMIT 3
            """, c=case_id, p=number)
            if pages:
                break
    for index, page in enumerate(pages, start=start + 1):
        body = " ".join(str(page.get("text") or "").split())[:1800]
        if body:
            items.append({
                "handle": f"L{index}", "source_type": "document_page",
                "document_id": page.get("document_id"), "page_number": page.get("page_number"),
                "folio": page.get("folio"), "filename": page.get("filename"),
                "text": body, "seed": "claim",
            })
    segments = rows(conn, """
        SELECT s.id AS segment_id, s.media_id, s.start_ms, s.end_ms, s.text,
               m.filename, sp.display_name AS speaker
          FROM transcript_segments s
          JOIN media m ON m.id = s.media_id
          LEFT JOIN speakers sp ON sp.id = s.speaker_id
         WHERE m.case_id = :c
           AND position(:p in immutable_unaccent(lower(s.text))) > 0
         ORDER BY s.start_ms LIMIT 3
    """, c=case_id, p=probe)
    for index, segment in enumerate(segments, start=start + len(items) + 1):
        body = " ".join(str(segment.get("text") or "").split())[:1200]
        if not body:
            continue
        payload = {
            "handle": f"L{index}", "source_type": "transcript_segment",
            "media_id": segment.get("media_id"), "segment_id": segment.get("segment_id"),
            "start_ms": segment.get("start_ms"), "end_ms": segment.get("end_ms"),
            "filename": segment.get("filename"), "speaker": segment.get("speaker"),
            "text": body, "seed": "claim",
        }
        if segment.get("start_ms") is not None:
            payload["start_mmss"] = text_terms.mmss(segment["start_ms"])
        items.append(payload)
    return items[:6]


def for_question(conn: Connection, case_id: str, question: str) -> list[dict[str, Any]]:
    claim = _match(conn, case_id, question)
    if not claim:
        return []
    items = contradiction_seed._citation_items(conn, str(claim["id"]), 0)
    if not items:
        items = _literal_items(conn, case_id, str(claim.get("text") or ""))
    claim_text = str(claim.get("text") or "")
    for item in items:
        item["claim_text"] = claim_text
    return items


__all__ = ["for_question"]
