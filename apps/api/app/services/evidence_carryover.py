"""Fuentes citadas en el turno anterior para seguimientos elípticos.

Un seguimiento como «¿y qué dijo Jorge Rojas sobre lo anterior?» no debe
depender de que la búsqueda vuelva a encontrar la misma página. Las citas que
el usuario ya vio son la referencia exacta de «lo anterior»: se resuelven a su
texto primario y se entregan al agente como evidencia inicial.

No crea hechos ni cambia la respuesta: solo recupera páginas/segmentos ya
citados, con el mismo documento, página y folio que se mostraron.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.services import query_rewrite, text_terms

MAX_ITEMS = 6
MAX_TEXT_CHARS = 4000


def _one(conn: Connection, sql: str, **params) -> dict[str, Any] | None:
    row = conn.execute(text(sql), params).first()
    return dict(row._mapping) if row else None


def _latest_citations(conn: Connection, session_id: str, case_id: str | None,
                      user_id: str | None) -> list[dict[str, Any]]:
    """Citas del último asistente con fuentes; si la sesión es nueva, la anterior."""
    row = _one(conn, """
        SELECT citations
          FROM chat_messages
         WHERE session_id = :s
           AND role = 'assistant'
           AND jsonb_array_length(coalesce(citations, '[]'::jsonb)) > 0
         ORDER BY created_at DESC, id DESC
         LIMIT 1
    """, s=session_id)
    if row and row.get("citations"):
        return list(row["citations"])
    if not case_id or not user_id:
        return []
    row = _one(conn, """
        SELECT m.citations
          FROM chat_messages m
          JOIN chat_sessions cs ON cs.id = m.session_id
         WHERE cs.case_id = :c
           AND cs.created_by = :u
           AND cs.archived_at IS NULL
           AND m.role = 'assistant'
           AND jsonb_array_length(coalesce(m.citations, '[]'::jsonb)) > 0
         ORDER BY m.created_at DESC, m.id DESC
         LIMIT 1
    """, c=case_id, u=user_id)
    return list(row["citations"]) if row and row.get("citations") else []


def _document_item(conn: Connection, citation: dict[str, Any], number: int) -> dict[str, Any] | None:
    document_id = citation.get("document_id")
    page = citation.get("page")
    if not document_id or page is None:
        return None
    page_row = _one(conn, """
        SELECT p.text, p.folio, p.ocr_confidence, d.filename
          FROM document_pages p
          JOIN documents d ON d.id = p.document_id
         WHERE p.document_id = :d AND p.page_number = :p
    """, d=str(document_id), p=int(page))
    if not page_row or not str(page_row.get("text") or "").strip():
        return None
    return {
        "handle": f"C{number}",
        "seed": "carryover",
        "source_type": "document_page",
        "document_id": str(document_id),
        "page_number": int(page),
        "folio": page_row.get("folio") or citation.get("folio"),
        "filename": page_row.get("filename") or citation.get("filename"),
        "ocr_confidence": page_row.get("ocr_confidence"),
        "text": str(page_row["text"])[:MAX_TEXT_CHARS],
    }


def _segment_item(conn: Connection, citation: dict[str, Any], number: int) -> dict[str, Any] | None:
    segment_id = citation.get("segment_id")
    if not segment_id:
        return None
    segment = _one(conn, """
        SELECT ts.text, ts.start_ms, ts.end_ms, m.filename, m.title
          FROM transcript_segments ts
          JOIN media m ON m.id = ts.media_id
         WHERE ts.id = :s
    """, s=str(segment_id))
    if not segment or not str(segment.get("text") or "").strip():
        return None
    start_ms = segment.get("start_ms")
    return {
        "handle": f"C{number}",
        "seed": "carryover",
        "source_type": "transcript_segment",
        "media_id": str(citation.get("media_id") or ""),
        "segment_id": str(segment_id),
        "filename": segment.get("filename"),
        "title": segment.get("title"),
        "start_ms": start_ms,
        "end_ms": segment.get("end_ms"),
        "start_mmss": text_terms.mmss(start_ms) if start_ms is not None else None,
        "speaker": citation.get("speaker"),
        "text": str(segment["text"])[:MAX_TEXT_CHARS],
    }


def for_followup(conn: Connection, session_id: str, question: str,
                 history: list[dict[str, Any]] | None, *,
                 case_id: str | None = None,
                 user_id: str | None = None,
                 limit: int = MAX_ITEMS) -> list[dict[str, Any]]:
    """Devuelve las fuentes citadas antes si la pregunta depende del contexto.

    La comprobación es deliberadamente conservadora: una pregunta nueva con
    tema propio no arrastra la evidencia anterior.
    """
    if not question or not history or not query_rewrite.needs_history(question, history):
        return []
    citations = _latest_citations(conn, session_id, case_id, user_id)
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for citation in citations:
        if not isinstance(citation, dict) or len(out) >= limit:
            continue
        if citation.get("document_id") and citation.get("page") is not None:
            key = ("doc", str(citation["document_id"]), int(citation["page"]))
            if key in seen:
                continue
            seen.add(key)
            item = _document_item(conn, citation, len(out) + 1)
        elif citation.get("segment_id"):
            key = ("seg", str(citation["segment_id"]))
            if key in seen:
                continue
            seen.add(key)
            item = _segment_item(conn, citation, len(out) + 1)
        else:
            continue
        if item is not None:
            out.append(item)
    return out


__all__ = ["for_followup"]
