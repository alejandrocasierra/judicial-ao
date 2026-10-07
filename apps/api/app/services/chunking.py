"""Chunking jurídico (SSD §39).

Un chunk es una unidad pequeña y coherente alineada a unidades procesales. Para
documentos OCR se parte cada página en fragmentos por sección (encabezados) de
~200-500 palabras, para que un chunk no sea una página entera. Cada chunk lleva
metadata enriquecida (documento, página, folio, entidades, hablante, parte) para
filtros y citas verificables.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.engine import Connection

from app.core.db import rows
from app.services import markdown


# Ventana para agrupar segmentos contiguos del mismo hablante (ms).
_TESTIMONY_GAP_MS = 30_000

# Tamaño objetivo de un chunk de documento (~200-500 palabras). Un chunk no corta a
# mitad de un bloque: se agrupan secciones cortas y, si una sección es muy larga, se
# parte por frases.
_CHUNK_MIN_WORDS = 180
_CHUNK_MAX_WORDS = 500
# Mínimo de palabras de un cuerpo para poder cerrar un chunk en un nuevo encabezado.
_MIN_BODY_WORDS = 40


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


def _doc_chunk(org_id: str, case_id: str, document_id: str, page_number: int,
               ocr_mode: str | None, folio: str | None, text: str, entities: list[str],
               part: int | None = None, parts: int | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "folio": folio,
        "ocr_mode": ocr_mode,
        "entities": entities,
    }
    if parts and parts > 1:
        metadata["part"] = part
        metadata["parts"] = parts
    return {
        "organization_id": org_id,
        "case_id": case_id,
        "chunk_type": "document_section",
        "document_id": document_id,
        "page_number": page_number,
        "media_id": None,
        "start_ms": None,
        "end_ms": None,
        "text": text,
        "metadata": metadata,
    }


def _word_count(text: str) -> int:
    return len(text.split())


def _split_long_text(text: str, max_words: int) -> list[str]:
    """Parte un texto largo en trozos <= max_words, cortando por frases (no a mitad de frase)."""
    if _word_count(text) <= max_words:
        return [text]
    sentences = re.split(r"(?<=[.;:!?])\s+", text)
    out: list[str] = []
    cur: list[str] = []
    cur_words = 0
    for s in sentences:
        w = _word_count(s)
        if cur and cur_words + w > max_words:
            out.append(" ".join(cur).strip())
            cur, cur_words = [], 0
        cur.append(s)
        cur_words += w
    if cur:
        out.append(" ".join(cur).strip())
    # Una sola frase que aún supere el máximo: corte duro por palabras.
    result: list[str] = []
    for piece in out:
        ws = piece.split()
        if len(ws) <= max_words:
            result.append(piece)
        else:
            result.extend(" ".join(ws[i:i + max_words]) for i in range(0, len(ws), max_words))
    return [p for p in result if p.strip()]


def chunk_page_text(text: str) -> list[str]:
    """Divide el texto de una página en fragmentos coherentes por sección.

    Reutiliza la estructura Markdown (encabezados, listas, párrafos) para no cortar
    a mitad de una idea. Agrupa secciones cortas hasta ~180 palabras y cierra el chunk
    en un nuevo encabezado; una sección que supere ~500 palabras se parte por frases."""
    md = markdown.text_to_markdown(text)
    if not md:
        return []

    # Unidades: (es_encabezado, texto). Los párrafos/listas se agrupan entre líneas en blanco;
    # un cuerpo muy largo se divide en varias unidades por frases.
    units: list[tuple[bool, str]] = []
    buffer: list[str] = []
    for line in md.split("\n"):
        if not line.strip():
            if buffer:
                units.extend((False, part) for part in _split_long_text("\n".join(buffer), _CHUNK_MAX_WORDS))
                buffer = []
        elif line.lstrip().startswith("#"):
            if buffer:
                units.extend((False, part) for part in _split_long_text("\n".join(buffer), _CHUNK_MAX_WORDS))
                buffer = []
            units.append((True, line.strip()))
        else:
            buffer.append(line)
    if buffer:
        units.extend((False, part) for part in _split_long_text("\n".join(buffer), _CHUNK_MAX_WORDS))

    chunks: list[str] = []
    cur: list[str] = []
    cur_words = 0
    cur_body_words = 0

    def flush() -> None:
        nonlocal cur, cur_words, cur_body_words
        if cur:
            text_block = "\n\n".join(cur).strip()
            # Un fragmento sin cuerpo (solo encabezados) se anexa al anterior.
            if cur_body_words == 0 < cur_words and chunks:
                chunks[-1] = f"{chunks[-1]}\n\n{text_block}"
            else:
                chunks.append(text_block)
        cur, cur_words, cur_body_words = [], 0, 0

    for is_heading, block in units:
        bw = _word_count(block)
        if cur:
            if cur_words + bw > _CHUNK_MAX_WORDS:
                flush()
            elif is_heading and cur_body_words >= _MIN_BODY_WORDS and cur_words >= _CHUNK_MIN_WORDS:
                flush()
        cur.append(block)
        cur_words += bw
        if not is_heading:
            cur_body_words += bw
    flush()
    return [c for c in chunks if c.strip()]


def _document_page_chunks(conn: Connection, org_id: str, case_id: str, document_id: str, page_number: int,
                          ocr_mode: str | None, folio: str | None, text: str) -> list[dict[str, Any]]:
    """Fragmenta el texto de una página en chunks por sección (uno o varios)."""
    frags = chunk_page_text(text)
    if not frags:
        return []
    entities = _entities_for_page(conn, case_id, document_id, page_number)
    total = len(frags)
    return [_doc_chunk(org_id, case_id, document_id, page_number, ocr_mode, folio, frag, entities, i + 1, total)
            for i, frag in enumerate(frags)]


def chunk_document(conn: Connection, org_id: str, case_id: str, document_id: str) -> list[dict[str, Any]]:
    """Chunks de un documento: fragmentos por sección, por página y motor OCR.

    Se indexan AMBOS resultados por motor (basico / document_ai) porque un expediente
    puede tener documentos procesados sólo con un motor; el modo va en la metadata para
    poder filtrar/comparar en la recuperación.

    Las páginas SIN fila en `document_ocr_versions` (p. ej. corregidas directamente
    sobre `document_pages`) se indexan igualmente desde su texto actual, con
    `ocr_mode` nulo, para que ninguna hoja quede sin chunk."""
    versions = rows(conn, """
        SELECT page_number, mode, text FROM document_ocr_versions
        WHERE document_id = :d ORDER BY page_number, mode
    """, d=document_id)
    versions_by_page: dict[int, list[dict[str, Any]]] = {}
    for v in versions:
        versions_by_page.setdefault(v["page_number"], []).append(v)

    pages = rows(conn, """
        SELECT page_number, folio, text FROM document_pages
        WHERE document_id = :d ORDER BY page_number
    """, d=document_id)

    chunks: list[dict[str, Any]] = []
    for p in pages:
        pn = p["page_number"]
        page_versions = versions_by_page.pop(pn, None)
        if page_versions:
            for v in page_versions:
                text = (v["text"] or "").strip()
                if text:
                    chunks.extend(_document_page_chunks(conn, org_id, case_id, document_id, pn, v["mode"], p["folio"], text))
        else:
            text = (p["text"] or "").strip()
            if text:
                chunks.extend(_document_page_chunks(conn, org_id, case_id, document_id, pn, None, p["folio"], text))

    # Versiones de páginas que no estén en document_pages (caso raro): no perderlas.
    for pn, page_versions in versions_by_page.items():
        for v in page_versions:
            text = (v["text"] or "").strip()
            if text:
                chunks.extend(_document_page_chunks(conn, org_id, case_id, document_id, pn, v["mode"], None, text))
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
