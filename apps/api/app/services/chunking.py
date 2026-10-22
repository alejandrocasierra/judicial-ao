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

from app.core.db import one, rows
from app.services import markdown


# Ventana para agrupar segmentos contiguos del mismo hablante (ms).
_TESTIMONY_GAP_MS = 30_000
# Techo de testimonio: un chunk de audio no debe superar ~350 palabras. Sin esto,
# una intervención larga del mismo hablante se convertía en un bloque gigante que
# desplazaba a las demás fuentes en el top-k.
_TESTIMONY_MAX_WORDS = 350
# Solape entre chunks de documento (~10 % del máximo) para no perder una frase
# que cae justo en la frontera. Se aplica solo al texto, nunca a la cita.
_CHUNK_OVERLAP_WORDS = 30

# Tamaño objetivo de un chunk de documento (~200-500 palabras). Un chunk no corta a
# mitad de un bloque: se agrupan secciones cortas y, si una sección es muy larga, se
# parte por frases.
_CHUNK_MIN_WORDS = 180
_CHUNK_MAX_WORDS = 500
# Mínimo de palabras de un cuerpo para poder cerrar un chunk en un nuevo encabezado.
_MIN_BODY_WORDS = 40


_ENTITY_CACHE: dict[tuple[int, str], tuple[dict[tuple[str, int], list[str]], dict[str, list[str]]]] = {}
_SPEAKER_CACHE: dict[tuple[int, str], dict[str, str | None]] = {}


def _entity_maps(conn: Connection, case_id: str) -> tuple[dict[tuple[str, int], list[str]], dict[str, list[str]]]:
    key = (id(conn), str(case_id))
    if key in _ENTITY_CACHE:
        return _ENTITY_CACHE[key]
    try:
        records = rows(conn, """
            SELECT c.document_id, c.page_number, c.segment_id, e.id AS entity_id
            FROM entities e
            JOIN citations c ON c.target_type = 'entity' AND c.target_id = e.id
            WHERE e.case_id = :c
              AND c.source_type IN ('document_page', 'transcript_segment')
        """, c=case_id)
    except AttributeError:
        return {}, {}
    by_page: dict[tuple[str, int], list[str]] = {}
    by_segment: dict[str, list[str]] = {}
    for r in records:
        eid = str(r["entity_id"])
        if r.get("document_id") is not None and r.get("page_number") is not None:
            by_page.setdefault((str(r["document_id"]), int(r["page_number"])), []).append(eid)
        if r.get("segment_id") is not None:
            by_segment.setdefault(str(r["segment_id"]), []).append(eid)
    for values in list(by_page.values()) + list(by_segment.values()):
        values[:] = sorted(set(values))
    _ENTITY_CACHE[key] = (by_page, by_segment)
    return by_page, by_segment


def _speaker_map(conn: Connection, case_id: str) -> dict[str, str | None]:
    key = (id(conn), str(case_id))
    if key in _SPEAKER_CACHE:
        return _SPEAKER_CACHE[key]
    try:
        records = rows(conn, "SELECT id, label, display_name FROM speakers WHERE case_id = :c", c=case_id)
    except AttributeError:
        return {}
    mapping = {str(r["id"]): (r.get("display_name") or r.get("label")) for r in records}
    _SPEAKER_CACHE[key] = mapping
    return mapping


def _entities_for_page(conn: Connection, case_id: str, document_id: str, page_number: int) -> list[str]:
    """Devuelve ids de entidades que citan esta página."""
    by_page, _ = _entity_maps(conn, case_id)
    return by_page.get((str(document_id), int(page_number)), [])


def _entities_for_segment(conn: Connection, case_id: str, segment_id: str) -> list[str]:
    _, by_segment = _entity_maps(conn, case_id)
    return by_segment.get(str(segment_id), [])


def _speaker_label(conn: Connection, speaker_id: str | None) -> str | None:
    """Nombre visible del hablante para los metadatos del chunk.

    Prefiere el nombre editado por el humano (`display_name`) y cae a la etiqueta
    de diarización (`SPK-xx`); así, al renombrar, la reindexación de pgvector queda
    con el nombre correcto."""
    if not speaker_id:
        return None
    # El case_id se obtiene del propio speaker; la caché de mapas usa el case_id
    # de la llamada, así que aquí se consulta una vez y se cachea por speaker.
    key = (id(conn), str(speaker_id))
    if key in _SPEAKER_CACHE:
        return _SPEAKER_CACHE[key]
    try:
        row = rows(conn, "SELECT label, display_name, case_id FROM speakers WHERE id = :s", s=speaker_id)
    except AttributeError:
        return None
    label = (row[0].get("display_name") or row[0]["label"]) if row else None
    _SPEAKER_CACHE[key] = label
    return label


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

    # Solape de frontera: el chunk siguiente repite la cola del anterior. No crea
    # una cita nueva; es contexto para que una frase partida siga siendo recuperable.
    if len(chunks) > 1:
        with_overlap: list[str] = [chunks[0]]
        for prev, current in zip(chunks, chunks[1:], strict=False):
            tail = prev.split()[-_CHUNK_OVERLAP_WORDS:]
            if tail and not current.startswith(" ".join(tail)):
                current = " ".join(tail) + "\n\n" + current
            with_overlap.append(current)
        chunks = with_overlap
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
    doc_row = one(conn, "SELECT ocr_mode FROM documents WHERE id = :d", d=document_id)
    doc_ocr_mode = doc_row.get("ocr_mode") if doc_row else None

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
                chunks.extend(_document_page_chunks(conn, org_id, case_id, document_id, pn, doc_ocr_mode, p["folio"], text))

    # Versiones de páginas que no estén en document_pages (caso raro): no perderlas.
    for pn, page_versions in versions_by_page.items():
        for v in page_versions:
            text = (v["text"] or "").strip()
            if text:
                chunks.extend(_document_page_chunks(conn, org_id, case_id, document_id, pn, v["mode"], None, text))
    return chunks


def chunk_media(conn: Connection, org_id: str, case_id: str, media_id: str) -> list[dict[str, Any]]:
    """Agrupa segmentos contiguos del mismo hablante en ventanas de testimonio."""
    media_row = one(conn, "SELECT asr_mode FROM media WHERE id = :m", m=media_id)
    asr_mode = media_row.get("asr_mode") if media_row else None
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
                or s["start_ms"] - current["end_ms"] > _TESTIMONY_GAP_MS
                or _word_count(current["text"] + "\n" + text) > _TESTIMONY_MAX_WORDS):
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
                    "asr_mode": asr_mode,
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


_SOURCE_CACHE: dict[tuple[int, str], dict[tuple[str, str], dict[str, Any]]] = {}


def _source_map(conn: Connection, case_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Todas las fuentes citables del caso, en una sola consulta (evita N+1)."""
    key = (id(conn), str(case_id))
    if key in _SOURCE_CACHE:
        return _SOURCE_CACHE[key]
    try:
        records = rows(conn, """
            SELECT c.target_type, c.target_id, c.document_id, c.page_number, c.media_id,
                   c.segment_id, ts.start_ms, ts.end_ms,
                   coalesce(d.filename, m.filename) AS filename, dp.folio
            FROM citations c
            LEFT JOIN documents d ON d.id = c.document_id
            LEFT JOIN media m ON m.id = c.media_id
            LEFT JOIN transcript_segments ts ON ts.id = c.segment_id
            LEFT JOIN document_pages dp ON dp.document_id = c.document_id
                                       AND dp.page_number = c.page_number
            WHERE c.case_id = :case_id
              AND (c.document_id IS NOT NULL OR c.media_id IS NOT NULL)
            ORDER BY c.created_at DESC
        """, case_id=case_id)
    except AttributeError:  # stub de tests sin conexión real
        return {}
    mapping: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        for rec in records:
            mapping.setdefault((str(rec["target_type"]), str(rec["target_id"])), dict(rec))
    except (KeyError, TypeError):  # stub de tests: filas sin columnas de citation
        return {}
    _SOURCE_CACHE[key] = mapping
    return mapping


def _source_for_target(conn: Connection, case_id: str, target_type: str,
                       target_id: str) -> dict[str, Any] | None:
    """Fuente primaria (documento+página o media+segmento) de un chunk estructurado.

    Sin fuente no se puede citar; el chunk se marca `citable=False` y no debe
    ocupar el top-k del chat. Así los claims/eventos dejan de desplazar a la
    evidencia primaria sin poder resolver una cita."""
    return _source_map(conn, case_id).get((str(target_type), str(target_id)))


def _structured_chunk(org_id: str, case_id: str, chunk_type: str, text: str, meta: dict[str, Any],
                      source: dict[str, Any] | None = None) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    source = source or {}
    citable = bool(source.get("document_id") or source.get("media_id"))
    metadata = {k: v for k, v in meta.items() if v is not None}
    metadata["citable"] = citable
    if source.get("filename"):
        metadata["source_filename"] = source["filename"]
    return {
        "organization_id": org_id,
        "case_id": case_id,
        "chunk_type": chunk_type,
        "document_id": source.get("document_id"),
        "page_number": source.get("page_number"),
        "media_id": source.get("media_id"),
        "start_ms": source.get("start_ms"),
        "end_ms": source.get("end_ms"),
        "text": text,
        "metadata": metadata,
    }


def chunk_claims(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, "SELECT id, text, claim_type, confidence FROM claims WHERE case_id = :c", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        src = _source_for_target(conn, case_id, "claim", str(r["id"]))
        ch = _structured_chunk(org_id, case_id, "claim", r["text"],
                               {"claim_type": r["claim_type"], "confidence": r["confidence"]}, src)
        if ch:
            out.append(ch)
    return out


def chunk_events(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, """
        SELECT id, description, event_type, event_date, date_precision, timeline_confidence
        FROM events WHERE case_id = :c""", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        src = _source_for_target(conn, case_id, "event", str(r["id"]))
        ch = _structured_chunk(org_id, case_id, "timeline_event", r["description"],
                               {"event_type": r["event_type"], "event_date": str(r["event_date"]) if r["event_date"] else None,
                                "date_precision": r["date_precision"], "timeline_confidence": r["timeline_confidence"]}, src)
        if ch:
            out.append(ch)
    return out


def chunk_facts(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, """
        SELECT f.id, f.proposition, f.status, f.proof_status, f.proof_basis,
               f.is_proven, f.fact_code, f.issue_id,
               (SELECT coalesce(json_agg(json_build_object(
                    'party_id', fp.party_id, 'side', fp.side, 'position', fp.position)),
                  '[]'::json)
                FROM fact_positions fp WHERE fp.fact_id = f.id) AS positions,
               (SELECT coalesce(json_agg(json_build_object(
                    'evidence_id', el.evidence_id, 'stance', el.stance)),
                  '[]'::json)
                FROM evidence_links el WHERE el.fact_id = f.id) AS evidence
        FROM facts f WHERE f.case_id = :c
    """, c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        src = _source_for_target(conn, case_id, "fact", str(r["id"]))
        meta = {
            "status": r.get("status"),
            "proof_status": r.get("proof_status") or "ALLEGED",
            "proof_basis": r.get("proof_basis") or "NONE",
            "is_proven": bool(r.get("is_proven")),
            "fact_code": r.get("fact_code"),
            "issue_id": str(r.get("issue_id")) if r.get("issue_id") else None,
            "party_positions": r.get("positions") or [],
            "evidence_links": r.get("evidence") or [],
        }
        ch = _structured_chunk(org_id, case_id, "fact", r["proposition"], meta, src)
        if ch:
            out.append(ch)
    return out


def chunk_decisions(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, """
        SELECT id, reasoning, decision_type, decision_date, outcome
        FROM decisions WHERE case_id = :c""", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        src = _source_for_target(conn, case_id, "decision", str(r["id"]))
        ch = _structured_chunk(org_id, case_id, "decision_reasoning", r["reasoning"],
                               {"decision_type": r["decision_type"],
                                "decision_date": str(r["decision_date"]) if r["decision_date"] else None,
                                "outcome": r["outcome"]}, src)
        if ch:
            out.append(ch)
    return out


def chunk_evidence(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, """
        SELECT id, description, evidence_type, source_document_id, source_media_id
        FROM evidence WHERE case_id = :c""", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        src = _source_for_target(conn, case_id, "evidence", str(r["id"]))
        ch = _structured_chunk(org_id, case_id, "evidence_description", r["description"],
                               {"evidence_type": r["evidence_type"],
                                "source_document_id": str(r["source_document_id"]) if r["source_document_id"] else None,
                                "source_media_id": str(r["source_media_id"]) if r["source_media_id"] else None}, src)
        if ch:
            out.append(ch)
    return out


def chunk_legal_rules(conn: Connection, org_id: str, case_id: str) -> list[dict[str, Any]]:
    records = rows(conn, """
        SELECT lr.id, lr.text, lr.jurisdiction, lr.source, lr.identifier
        FROM legal_rules lr
        JOIN cases c ON c.id = :c
        WHERE lr.organization_id = c.organization_id
          AND lr.jurisdiction = c.jurisdiction
          AND lr.is_active""", c=case_id)
    out: list[dict[str, Any]] = []
    for r in records:
        src = _source_for_target(conn, case_id, "legal_rule", str(r["id"]))
        ch = _structured_chunk(org_id, case_id, "legal_rule", r["text"],
                               {"jurisdiction": r["jurisdiction"], "source": r["source"], "identifier": r["identifier"]}, src)
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
