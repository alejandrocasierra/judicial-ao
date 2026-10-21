"""Siembra de evidencia para una contradicción concreta.

El agente puede recuperar una versión y perder la otra: la respuesta entonces
dice que un documento «no existe» o cubre solo un lado. Si la pregunta describe
una contradicción que YA está en la base, sabemos exactamente qué dos claims la
forman y qué páginas/segmentos las sustentan. Esa evidencia entra como semilla
de primera clase, no como una búsqueda más.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.engine import Connection

from app.core.db import rows
from app.services import text_terms


def _match(conn: Connection, case_id: str, question: str) -> dict[str, Any] | None:
    folded = text_terms.norm(question)
    if "contradic" not in folded:
        return None
    contras = rows(conn, """
        SELECT id::text, description, claim_a_id::text AS a, claim_b_id::text AS b
          FROM contradictions WHERE case_id = :c
    """, c=case_id)
    best: tuple[float, dict] | None = None
    q_terms = set(text_terms.content_terms(question))
    for contra in contras:
        desc = str(contra.get("description") or "")
        head = text_terms.norm(desc)[:90]
        d_terms = set(text_terms.content_terms(desc))
        overlap = len(q_terms & d_terms) / len(d_terms) if d_terms else 0.0
        exact = bool(head and head in folded)
        if exact or overlap >= 0.55:
            score = 1.0 if exact else overlap
            if best is None or score > best[0]:
                best = (score, contra)
    return best[1] if best else None


def _citation_items(conn: Connection, claim_id: str, start: int) -> list[dict[str, Any]]:
    found = rows(conn, """
        SELECT c.id::text, c.source_type, c.document_id, c.page_number, c.folio,
               c.media_id, c.segment_id, c.start_ms, c.end_ms,
               coalesce(dp.text, ts.text) AS text,
               coalesce(d.filename, m.filename) AS filename,
               sp.display_name AS speaker
          FROM citations c
          LEFT JOIN document_pages dp ON dp.document_id = c.document_id
                                    AND dp.page_number = c.page_number
          LEFT JOIN documents d ON d.id = c.document_id
          LEFT JOIN transcript_segments ts ON ts.id = c.segment_id
          LEFT JOIN media m ON m.id = c.media_id
          LEFT JOIN speakers sp ON sp.id = ts.speaker_id
         WHERE c.target_type = 'claim' AND c.target_id = :i
         ORDER BY c.page_number NULLS LAST, c.start_ms NULLS LAST
         LIMIT 4
    """, i=claim_id)
    out: list[dict[str, Any]] = []
    for index, item in enumerate(found):
        text = " ".join(str(item.get("text") or "").split())[:1600]
        if not text:
            continue
        payload = {
            "handle": f"S{start + index + 1}",
            "source_type": item.get("source_type") or "document_page",
            "text": text,
            "filename": item.get("filename"),
            "page_number": item.get("page_number"),
            "folio": item.get("folio"),
            "document_id": item.get("document_id"),
            "media_id": item.get("media_id"),
            "segment_id": item.get("segment_id"),
            "start_ms": item.get("start_ms"),
            "end_ms": item.get("end_ms"),
            "speaker": item.get("speaker"),
            "seed": "contradiction",
        }
        if payload["source_type"] == "transcript_segment" and item.get("start_ms") is not None:
            payload["start_mmss"] = text_terms.mmss(item["start_ms"])
        out.append(payload)
    return out


def _context_snippet(text: str) -> str:
    """Fragmento literal que explica la versión (tasa, capitalización, título en blanco)."""
    folded = text.lower()
    for term in ("1,35", "1.35", "capital debido", "capitalizaci", "en blanco", "interes"):
        index = folded.find(term)
        if index >= 0:
            return " ".join(text[max(0, index - 240):index + 480].split())
    return ""


def _literal_items(conn: Connection, case_id: str, text: str,
                   start: int = 0) -> list[dict[str, Any]]:
    """Fallback cuando el claim no tiene citas: buscar su texto en la fuente."""
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
         ORDER BY dp.page_number LIMIT 2
    """, c=case_id, p=probe)
    if not pages:
        # Las cifras son el ancla más estable cuando el claim está parafraseado
        # o el OCR cambió las palabras.
        for number in sorted(set(re.findall(r"\d[\d.,]{5,}", text)), key=len, reverse=True)[:2]:
            pages = rows(conn, """
                SELECT dp.document_id, dp.page_number, dp.folio, d.filename, dp.text
                  FROM document_pages dp
                  JOIN documents d ON d.id = dp.document_id
                 WHERE d.case_id = :c AND position(:p in dp.text) > 0
                 ORDER BY dp.page_number LIMIT 2
            """, c=case_id, p=number)
            if pages:
                break
    for index, page in enumerate(pages, start=start + 1):
        body = " ".join(str(page.get("text") or "").split())[:1800]
        if body:
            items.append({
                "handle": f"C{index}", "source_type": "document_page",
                "document_id": page.get("document_id"), "page_number": page.get("page_number"),
                "folio": page.get("folio"), "filename": page.get("filename"),
                "text": body, "seed": "contradiction",
            })
    return items


def for_question(conn: Connection, case_id: str, question: str) -> list[dict[str, Any]]:
    """Evidencia primaria de las dos versiones, si la pregunta identifica una contradicción."""
    contra = _match(conn, case_id, question)
    if not contra:
        return []
    items: list[dict[str, Any]] = []
    claim_texts: list[str] = []
    for claim_id in (contra.get("a"), contra.get("b")):
        if not claim_id:
            continue
        claim = rows(conn, "SELECT text FROM claims WHERE id = :i", i=str(claim_id))
        claim_text = str(claim[0].get("text") or "") if claim else ""
        if claim_text:
            claim_texts.append(claim_text)
        claim_items = _citation_items(conn, str(claim_id), len(items))
        if not claim_items and claim_text:
            claim_items = _literal_items(conn, case_id, claim_text, len(items))
        for item in claim_items:
            item["claim_text"] = claim_text
            if not item.get("context_text"):
                item["context_text"] = _context_snippet(str(item.get("text") or ""))
        items.extend(claim_items)
    # Dedupe por fuente primaria.
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
    # Las dos versiones pueden compartir página; el fallback necesita ambos
    # textos, aunque la fuente sea la misma.
    if out:
        present = {str(item.get("claim_text") or "") for item in out}
        for claim_text in claim_texts:
            if claim_text and claim_text not in present:
                clone = dict(out[0])
                clone["claim_text"] = claim_text
                out.append(clone)
                present.add(claim_text)
    return out[:8]


__all__ = ["for_question"]
