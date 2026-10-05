"""Chunking jurídico (SSD §39).

Un chunk nunca es un corte genérico de N tokens: se alinea a unidades procesales
(página/folio, segmento de testimonio, claim, evento, decisión, prueba, norma).
Cada chunk lleva metadata enriquecida (documento, página, folio, entidades,
hablante, etc.) para filtros y citas verificables.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Connection

from app.core.db import rows


# Ventana para agrupar segmentos contiguos del mismo hablante (ms).
_TESTIMONY_GAP_MS = 30_000


def _entities_for_page(conn: Connection, case_id: str, document_id: str, page_number: int) -> list[str]:
    """Devuelve ids de entidades que citan esta página."""
    return [str(r["entity_id"]) for r in rows(conn, """
        SELECT DISTINCT e.id AS entity_id
        FROM entities e
        JOIN citations c ON c.target_type = 'entity' AND c.target_id = e.id
        WHERE e.case_id = :c AND c.source_type = 'document_page'
          AND c.document_id = :d AND c.page_number = :p
    """, c=case_id, d=document_id, p=page_number)]


def _entities_for_segment(conn: Connection, case_id: str, segment_id: str) -> list[str]:
    return [str(r["entity_id"]) for r in rows(conn, """
        SELECT DISTINCT e.id AS entity_id
        FROM entities e
        JOIN citations c ON c.target_type = 'entity' AND c.target_id = e.id
        WHERE e.case_id = :c AND c.source_type = 'transcript_segment'
          AND c.segment_id = :s
    """, c=case_id, s=segment_id)]


def _speaker_label(conn: Connection, speaker_id: str | None) -> str | None:
    """Nombre visible del hablante para los metadatos del chunk.

    Prefiere el nombre editado por el humano (`display_name`) y cae a la etiqueta
    de diarización (`SPK-xx`); así, al renombrar, la reindexación de pgvector queda
    con el nombre correcto."""
    if not speaker_id:
        return None
    row = rows(conn, "SELECT label, display_name FROM speakers WHERE id = :s", s=speaker_id)
    if not row:
        return None
    return row[0].get("display_name") or row[0]["label"]


def chunk_document(conn: Connection, org_id: str, case_id: str, document_id: str) -> list[dict[str, Any]]:
    """Un chunk por página Y por motor OCR (basico / document_ai).

    Se indexan AMBOS resultados (no solo el "actual") porque un expediente puede
    tener documentos procesados sólo con un motor; el modo va en la metadata para
    poder filtrar/comparar en la recuperación.
    """
    folios = {
        r["page_number"]: r["folio"]
        for r in rows(conn, "SELECT page_number, folio FROM document_pages WHERE document_id = :d", d=document_id)
    }
    versions = rows(conn, """
        SELECT page_number, mode, text FROM document_ocr_versions
        WHERE document_id = :d ORDER BY page_number, mode
    """, d=document_id)

    chunks: list[dict[str, Any]] = []
    if versions:
        for v in versions:
            text = (v["text"] or "").strip()
            if not text:
                continue
            chunks.append({
                "organization_id": org_id,
                "case_id": case_id,
                "chunk_type": "document_section",
                "document_id": document_id,
                "page_number": v["page_number"],
                "media_id": None,
                "start_ms": None,
                "end_ms": None,
                "text": text,
                "metadata": {
                    "folio": folios.get(v["page_number"]),
                    "ocr_mode": v["mode"],
                    "entities": _entities_for_page(conn, case_id, document_id, v["page_number"]),
                },
            })
        return chunks

    # Compatibilidad: documentos sin versiones por motor -> texto actual de la página.
    pages = rows(conn, """
        SELECT page_number, folio, text FROM document_pages
        WHERE document_id = :d ORDER BY page_number
    """, d=document_id)
    for p in pages:
        text = (p["text"] or "").strip()
        if not text:
            continue
        chunks.append({
            "organization_id": org_id,
            "case_id": case_id,
            "chunk_type": "document_section",
            "document_id": document_id,
            "page_number": p["page_number"],
            "media_id": None,
            "start_ms": None,
            "end_ms": None,
            "text": text,
            "metadata": {
                "folio": p["folio"],
                "ocr_mode": None,
                "entities": _entities_for_page(conn, case_id, document_id, p["page_number"]),
            },
        })
    return chunks


def chunk_media(conn: Connection, org_id: str, case_id: str, media_id: str) -> list[dict[str, Any]]:
    """Agrupa segmentos contiguos del mismo hablante en ventanas de testimonio."""
    segs = rows(conn, """
        SELECT id, speaker_id, start_ms, end_ms, text FROM transcript_segments
        WHERE media_id = :m ORDER BY start_ms
    """, m=media_id)
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current and current["text"]:
            chunks.append(current)
            current = None

    for s in segs:
        text = (s["text"] or "").strip()
        if not text:
            continue
        speaker_label = _speaker_label(conn, s["speaker_id"])
        if (current is None
                or speaker_label != current["metadata"].get("speaker")
                or s["start_ms"] - current["end_ms"] > _TESTIMONY_GAP_MS):
            flush()
            current = {
                "organization_id": org_id,
                "case_id": case_id,
                "chunk_type": "testimony_segment",
                "document_id": None,
                "page_number": None,
                "media_id": media_id,
                "start_ms": s["start_ms"],
                "end_ms": s["end_ms"],
                "text": text,
                "metadata": {
                    "segment_id": str(s["id"]),
                    "speaker": speaker_label,
                    "entities": _entities_for_segment(conn, case_id, str(s["id"])),
                },
            }
        else:
            current["text"] += "\n" + text
            current["end_ms"] = s["end_ms"]
            current["metadata"]["entities"] = list(set(current["metadata"]["entities"])
                                                   | set(_entities_for_segment(conn, case_id, str(s["id"]))))
    flush()
    return chunks


def _structured_chunk(org_id: str, case_id: str, chunk_type: str, text: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    return {
        "organization_id": org_id,
        "case_id": case_id,
        "chunk_type": chunk_type,
        "document_id": None,
        "page_number": None,
        "media_id": None,
        "start_ms": None,
        "end_ms": None,
        "text": text,
        "metadata": {k: v for k, v in meta.items() if v is not None},
    }


def chunk_claims(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, "SELECT id, text, claim_type, confidence FROM claims WHERE case_id = :c", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        ch = _structured_chunk(org_id, case_id, "claim", r["text"],
                               {"claim_type": r["claim_type"], "confidence": r["confidence"]})
        if ch:
            out.append(ch)
    return out


def chunk_events(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, """
        SELECT id, description, event_type, event_date, date_precision, timeline_confidence
        FROM events WHERE case_id = :c""", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        ch = _structured_chunk(org_id, case_id, "timeline_event", r["description"],
                               {"event_type": r["event_type"], "event_date": str(r["event_date"]) if r["event_date"] else None,
                                "date_precision": r["date_precision"], "timeline_confidence": r["timeline_confidence"]})
        if ch:
            out.append(ch)
    return out


def chunk_facts(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, "SELECT id, proposition, status FROM facts WHERE case_id = :c", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        ch = _structured_chunk(org_id, case_id, "paragraph", r["proposition"], {"status": r["status"]})
        if ch:
            out.append(ch)
    return out


def chunk_decisions(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, """
        SELECT id, reasoning, decision_type, decision_date, outcome
        FROM decisions WHERE case_id = :c""", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        ch = _structured_chunk(org_id, case_id, "decision_reasoning", r["reasoning"],
                               {"decision_type": r["decision_type"],
                                "decision_date": str(r["decision_date"]) if r["decision_date"] else None,
                                "outcome": r["outcome"]})
        if ch:
            out.append(ch)
    return out


def chunk_evidence(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, """
        SELECT id, description, evidence_type, source_document_id, source_media_id
        FROM evidence WHERE case_id = :c""", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        ch = _structured_chunk(org_id, case_id, "evidence_description", r["description"],
                               {"evidence_type": r["evidence_type"],
                                "source_document_id": str(r["source_document_id"]) if r["source_document_id"] else None,
                                "source_media_id": str(r["source_media_id"]) if r["source_media_id"] else None})
        if ch:
            out.append(ch)
    return out


def chunk_legal_rules(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, """
        SELECT id, text, jurisdiction, source, identifier
        FROM legal_rules WHERE case_id = :c""", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        ch = _structured_chunk(org_id, case_id, "legal_rule", r["text"],
                               {"jurisdiction": r["jurisdiction"], "source": r["source"], "identifier": r["identifier"]})
        if ch:
            out.append(ch)
    return out


def build_case_chunks(conn: Connection, org_id: str, case_id: str,
                      include_structured: bool = True) -> list[dict[str, Any]]:
    """Genera todos los chunks de un caso: documentos, media y entidades extraídas."""
    chunks: list[dict[str, Any]] = []
    docs = rows(conn, "SELECT id FROM documents WHERE case_id = :c", c=case_id)
    for d in docs:
        chunks.extend(chunk_document(conn, org_id, case_id, str(d["id"])))
    media = rows(conn, "SELECT id FROM media WHERE case_id = :c", c=case_id)
    for m in media:
        chunks.extend(chunk_media(conn, org_id, case_id, str(m["id"])))
    if include_structured:
        chunks.extend(chunk_claims(conn, org_id, case_id))
        chunks.extend(chunk_events(conn, org_id, case_id))
        chunks.extend(chunk_facts(conn, org_id, case_id))
        chunks.extend(chunk_decisions(conn, org_id, case_id))
        chunks.extend(chunk_evidence(conn, org_id, case_id))
    return chunks
