from __future__ import annotations

import hashlib
import logging
import time
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.schemas import DeletionRequestIn, DocumentPagePatch, TranscriptSegmentPatch
from app.security.deps import Principal, case_access, current_principal, download_principal, require_org
from app.services import audit, file_delete, files, indexing, markdown, ocr_confidence, ocr_lexicon, ratelimit
from app.services.storage import incoming_dir, key_from_uri, original_key, storage
from app.workers.dispatcher import create_job, enqueue_if_pending
from app.workers.handlers.file_ingest import enqueue_graph_refresh

router = APIRouter(prefix="/cases/{case_id}", tags=["documents"])
log = logging.getLogger(__name__)


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    buf, total = bytearray(), 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise AppError("UPLOAD_TOO_LARGE", 413)
        buf.extend(chunk)
    if total == 0:
        raise AppError("UPLOAD_EMPTY", 422)
    return bytes(buf)


def _validate(data: bytes, filename: str, declared: str | None, allowed: set[str]) -> str:
    ext = files.extension(filename)
    if ext not in allowed:
        raise AppError("UPLOAD_TYPE_NOT_ALLOWED", 415)
    real = files.sniff(data)
    if real != ext or (declared and declared not in (files.MIME[real], "application/octet-stream")):
        raise AppError("UPLOAD_CONTENT_MISMATCH", 415)
    try:
        files.scan(data)
    except files.MalwareFound:
        raise AppError("UPLOAD_MALWARE_DETECTED", 422) from None
    return real


async def _ingest(kind: str, case_id: UUID, file: UploadFile, request: Request, p: Principal, title: str | None):
    s = get_settings()
    perm = "document.upload" if kind == "document" else "media.upload"
    case = case_access(p, case_id, perm)
    if case["status"] == "ARCHIVED":
        raise AppError("INVALID_STATE_TRANSITION", 409)
    ratelimit.check("upload", p.user_id)
    ratelimit.check_org("upload", p.org_id)
    limit = s.UPLOAD_MAX_BYTES_DOCUMENT if kind == "document" else s.UPLOAD_MAX_BYTES_MEDIA
    allowed = s.allowed_document_types if kind == "document" else s.allowed_media_types
    data = await _read_limited(file, limit)
    filename = files.sanitize_filename(file.filename or "")
    real = _validate(data, filename, file.content_type, allowed)
    sha = hashlib.sha256(data).hexdigest()
    table = "documents" if kind == "document" else "media"
    with tx(p.org_id, p.user_id) as c:
        dup = one(c, f"SELECT id FROM {table} WHERE case_id = :c AND sha256 = :h AND filename = :f",
                  c=str(case_id), h=sha, f=filename)
        if dup:
            raise AppError("DOCUMENT_DUPLICATE", 409, {"existing_id": str(dup["id"])})
        uri = storage().put(original_key(str(case_id), sha, kind), data)
        # Copia local compartida (api<->worker): el worker hace OCR/ASR sin re-descargar
        # de GCS y la borra al terminar (disco limitado).
        try:
            (incoming_dir() / sha).write_bytes(data)
        except Exception:  # noqa: BLE001
            log.debug("no se pudo dejar copia local de %s", filename, exc_info=True)
        if kind == "document":
            row = one(c, """INSERT INTO documents (organization_id, case_id, storage_uri, sha256, size_bytes, mime_type, filename, uploaded_by)
                VALUES (:o,:c,:u,:h,:sz,:m,:f,:by) RETURNING id, sha256, size_bytes, mime_type, filename, processing_status, created_at""",
                      o=p.org_id, c=str(case_id), u=uri, h=sha, sz=len(data), m=files.MIME[real], f=filename, by=p.user_id)
        else:
            row = one(c, """INSERT INTO media (organization_id, case_id, storage_uri, sha256, size_bytes, mime_type, filename, title, media_type, uploaded_by)
                VALUES (:o,:c,:u,:h,:sz,:m,:f,:t,:mt,:by) RETURNING id, sha256, size_bytes, mime_type, filename, media_type, processing_status, created_at""",
                      o=p.org_id, c=str(case_id), u=uri, h=sha, sz=len(data), m=files.MIME[real], f=filename, t=title,
                      mt="video" if files.MIME[real].startswith("video") else "audio", by=p.user_id)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action=f"{kind}.uploaded", entity_type=kind,
                     entity_id=str(row["id"]), after={"sha256": sha, "size": len(data)}, request=request)
    return row


@router.post("/documents", status_code=201)
async def upload_document(case_id: UUID, request: Request, file: UploadFile = File(...), p: Principal = Depends(current_principal)):
    return await _ingest("document", case_id, file, request, p, None)


@router.post("/media", status_code=201)
async def upload_media(case_id: UUID, request: Request, file: UploadFile = File(...),
                       title: str | None = Form(default=None, max_length=200), p: Principal = Depends(current_principal)):
    return await _ingest("media", case_id, file, request, p, title)


@router.get("/documents")
def list_documents(case_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "document.read")
    with tx(p.org_id, p.user_id) as c:
        return rows(c, """SELECT id, filename, mime_type, size_bytes, sha256, document_type, document_type_confidence, document_date,
            page_count, folio_start, folio_end, language, processing_status, deletion_requested_at, created_at,
            cuaderno, indice_numero, indice_nombre_original, sub_orden, orden_procesal, es_indice_maestro
            FROM documents WHERE case_id = :c ORDER BY orden_procesal NULLS LAST, created_at""", c=str(case_id))


@router.get("/documents/{document_id}")
def get_document(case_id: UUID, document_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "document.read")
    with tx(p.org_id, p.user_id) as c:
        doc = one(c, """SELECT id, filename, mime_type, size_bytes, document_type,
            page_count, processing_status, ocr_mode, created_at
            FROM documents WHERE id = :d AND case_id = :c""", d=str(document_id), c=str(case_id))
        if not doc:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
    return doc


@router.get("/documents/{document_id}/ocr-versions")
def list_ocr_versions(case_id: UUID, document_id: UUID, p: Principal = Depends(current_principal)):
    """Lista los motores OCR que ya procesaron este documento (para el select del visor)."""
    case_access(p, case_id, "document.read")
    with tx(p.org_id, p.user_id) as c:
        doc = one(c, "SELECT id, ocr_mode, processing_status FROM documents WHERE id = :d AND case_id = :c",
                  d=str(document_id), c=str(case_id))
        if not doc:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        versions = rows(c, """SELECT mode, count(*) AS pages,
                               round(avg(ocr_confidence), 3) AS confidence_avg,
                               max(created_at) AS last_run
                        FROM document_ocr_versions
                        WHERE document_id = :d
                        GROUP BY mode""", d=str(document_id))
    return {
        "document_id": str(document_id),
        "current_mode": doc["ocr_mode"],
        "processing_status": doc["processing_status"],
        "versions": [{"mode": v["mode"], "pages": v["pages"],
                      "confidence_avg": float(v["confidence_avg"]) if v["confidence_avg"] is not None else None,
                      "last_run": v["last_run"]} for v in versions],
    }


@router.get("/documents/{document_id}/pages/{page_number}")
def get_page(case_id: UUID, document_id: UUID, page_number: int, request: Request,
             mode: str | None = None,
             p: Principal = Depends(current_principal)):
    """Texto OCR de una página. `mode` (basico|document_ai) devuelve la versión
    guardada de ese motor si existe; si no, la versión principal."""
    case_access(p, case_id, "document.read")
    with tx(p.org_id, p.user_id) as c:
        if mode in ("basico", "document_ai"):
            page = one(c, """SELECT v.page_number, NULL AS folio, v.text, v.ocr_confidence,
                    false AS needs_review, v.layout_json
                FROM document_ocr_versions v
                JOIN documents d ON d.id = v.document_id
                WHERE d.case_id = :c AND v.document_id = :d AND v.page_number = :n AND v.mode = :m""",
                c=str(case_id), d=str(document_id), n=page_number, m=mode)
            if page:
                page = dict(page)
                page["mode"] = mode
                return page
        page = one(c, """SELECT p.page_number, p.folio, p.text, p.ocr_confidence, p.needs_review, p.layout_json
            FROM document_pages p JOIN documents d ON d.id = p.document_id
            WHERE d.case_id = :c AND p.document_id = :d AND p.page_number = :n""", c=str(case_id), d=str(document_id), n=page_number)
        if not page:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="document.page_read", entity_type="document",
                     entity_id=str(document_id), after={"page": page_number, "mode": mode}, request=request)
    return page


@router.get("/documents/{document_id}/pages")
def list_pages(case_id: UUID, document_id: UUID, mode: str | None = None,
               p: Principal = Depends(current_principal)):
    """Lista de hojas. Con `mode` (basico|document_ai) devuelve la confianza y el
    tamaño de esa versión, para ver de un vistazo qué hojas se editaron."""
    case_access(p, case_id, "document.read")
    with tx(p.org_id, p.user_id) as c:
        doc = one(c, "SELECT filename, page_count FROM documents WHERE id = :d AND case_id = :c", d=str(document_id), c=str(case_id))
        if not doc:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        if mode in ("basico", "document_ai"):
            pages = rows(c, """SELECT p.page_number, p.folio, false AS needs_review,
                               coalesce(v.ocr_confidence, p.ocr_confidence) AS ocr_confidence,
                               (p.image_uri IS NOT NULL AND p.image_uri <> '') AS has_image,
                               length(coalesce(v.text, p.text, '')) AS chars,
                               p.human_corrected, (v.id IS NOT NULL) AS has_version
                               FROM document_pages p
                               LEFT JOIN document_ocr_versions v
                                 ON v.document_id = p.document_id AND v.page_number = p.page_number AND v.mode = :m
                               WHERE p.document_id = :d ORDER BY p.page_number""",
                         d=str(document_id), m=mode)
        else:
            pages = rows(c, """SELECT p.page_number, p.folio, p.needs_review, p.ocr_confidence,
                               (p.image_uri IS NOT NULL AND p.image_uri <> '') AS has_image,
                               length(coalesce(p.text,'')) AS chars, p.human_corrected
                               FROM document_pages p WHERE p.document_id = :d ORDER BY p.page_number""",
                         d=str(document_id))
    return {"document_id": str(document_id), "filename": doc["filename"], "mode": mode, "pages": pages}


@router.get("/documents/{document_id}/markdown")
def get_document_markdown(case_id: UUID, document_id: UUID, p: Principal = Depends(current_principal)):
    """Markdown del documento (metadatos + una sección por página con jerarquía).

    Representación legible/semántica para RAG, lectura por LLM y exportación."""
    case_access(p, case_id, "document.read")
    with tx(p.org_id, p.user_id) as c:
        md = markdown.document_markdown(c, str(case_id), str(document_id))
        if md is None:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
    return {"document_id": str(document_id), "markdown": md}


@router.get("/documents/{document_id}/chunks")
def list_document_chunks(case_id: UUID, document_id: UUID, mode: str | None = None,
                         p: Principal = Depends(current_principal)):
    """Chunks RAG del documento (unidades indexadas), ordenados por página.

    `mode` filtra por motor OCR (`basico`/`document_ai`); los chunks sin motor
    (documentos antiguos, sin versiones por motor) también se incluyen. Cada chunk
    se devuelve con su texto crudo y su representación Markdown para visualizarlo."""
    case_access(p, case_id, "document.read")
    with tx(p.org_id, p.user_id) as c:
        doc = one(c, "SELECT id FROM documents WHERE id = :d AND case_id = :c",
                  d=str(document_id), c=str(case_id))
        if not doc:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        chunks = rows(c, """
            SELECT id, chunk_type, page_number, start_ms, end_ms, text, metadata
              FROM chunks
             WHERE case_id = :c AND document_id = :d
               AND (CAST(:m AS text) IS NULL OR metadata->>'ocr_mode' IS NULL OR metadata->>'ocr_mode' = CAST(:m AS text))
             ORDER BY page_number NULLS LAST, COALESCE((metadata->>'part')::int, 0), created_at
        """, c=str(case_id), d=str(document_id), m=mode)
    for ch in chunks:
        meta = ch.pop("metadata", None) or {}
        ch["folio"] = meta.get("folio")
        ch["ocr_mode"] = meta.get("ocr_mode")
        ch["entities"] = meta.get("entities") or []
        ch["part"] = meta.get("part")
        ch["parts"] = meta.get("parts")
        ch["markdown"] = markdown.text_to_markdown(ch.get("text"))
    return {"document_id": str(document_id), "mode": mode, "chunks": chunks}


@router.patch("/documents/{document_id}/pages/{page_number}")
def update_page(case_id: UUID, document_id: UUID, page_number: int, body: DocumentPagePatch, request: Request,
                p: Principal = Depends(current_principal)):
    """Corrige el texto OCR de una página/motor.

    La confianza arranca al 100% para el modo procesado y baja de forma proporcional al
    contenido editado (puntuación/espacios no cuentan). Cada modo (`basico`/`document_ai`)
    es independiente. La corrección humana alimenta el lexicón, reindexa pgvector
    (chunks + embeddings) y encola la reconstrucción del knowledge graph."""
    case_access(p, case_id, "document.upload")
    with tx(p.org_id, p.user_id) as c:
        doc = one(c, "SELECT id, ocr_mode FROM documents WHERE id = :d AND case_id = :c",
                  d=str(document_id), c=str(case_id))
        if not doc:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        explicit_mode = body.mode
        effective_mode = explicit_mode or doc["ocr_mode"]
        # Confianza previa del scope que se está editando.
        if explicit_mode:
            prev = one(c, """SELECT text, ocr_confidence FROM document_ocr_versions
                             WHERE document_id = :d AND page_number = :n AND mode = :m""",
                       d=str(document_id), n=page_number, m=explicit_mode)
        else:
            prev = one(c, """SELECT text, ocr_confidence FROM document_pages
                             WHERE document_id = :d AND page_number = :n""",
                       d=str(document_id), n=page_number)
        if not prev:
            # El motor aún no tiene versión guardada: parte del texto de la página actual.
            prev = one(c, """SELECT text, ocr_confidence FROM document_pages
                             WHERE document_id = :d AND page_number = :n""",
                       d=str(document_id), n=page_number)
        if not prev:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        new_conf = ocr_confidence.confidence_after_edit(prev["ocr_confidence"], prev["text"], body.text)
        metrics = ocr_confidence.edit_metrics(prev["text"], body.text)

        def _upsert_version(mode: str) -> None:
            one(c, """INSERT INTO document_ocr_versions
                        (organization_id, document_id, page_number, mode, text, ocr_confidence)
                      VALUES (:o, :d, :n, :m, :t, :conf)
                      ON CONFLICT (document_id, page_number, mode) DO UPDATE SET
                        text = EXCLUDED.text, ocr_confidence = EXCLUDED.ocr_confidence, created_at = now()
                      RETURNING id""",
                o=p.org_id, d=str(document_id), n=page_number, m=mode, t=body.text, conf=new_conf)

        def _update_current_page() -> None:
            one(c, """UPDATE document_pages
                      SET text = :t, ocr_confidence = :conf, needs_review = false,
                          human_corrected = true, corrected_at = now(), corrected_by = :u
                      WHERE document_id = :d AND page_number = :n RETURNING id""",
                t=body.text, conf=new_conf, d=str(document_id), n=page_number, u=str(p.user_id))

        if explicit_mode:
            # Se edita ese motor; la página "actual" sólo se toca si coincide con el motor actual.
            _upsert_version(explicit_mode)
            if doc["ocr_mode"] == explicit_mode:
                _update_current_page()
        else:
            _update_current_page()
            if effective_mode in ("basico", "document_ai"):
                _upsert_version(effective_mode)
        learned = ocr_lexicon.learn_terms(c, p.org_id, body.text)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="document.page_corrected", entity_type="document",
                     entity_id=str(document_id),
                     after={"page": page_number, "mode": effective_mode, "confidence": new_conf,
                            "learned_terms": learned}, request=request)
    with tx(p.org_id, p.user_id) as c:
        try:
            indexing.index_document(c, p.org_id, str(case_id), str(document_id), actor_id=p.user_id)
        except Exception:
            log.exception("no se pudo reindexar el documento %s tras corregir la página", document_id)
    # Knowledge graph: reconstrucción con ventana de corrección (no rompe el guardado si el broker cae).
    try:
        enqueue_graph_refresh(str(p.org_id), str(case_id), str(p.user_id), correction=True)
    except Exception:
        log.exception("no se pudo encolar graph_build tras corregir la página %s del documento %s",
                      page_number, document_id)
    return {"document_id": str(document_id), "page_number": page_number, "mode": effective_mode,
            "confidence": new_conf, "chars_total": metrics["total"], "chars_changed": metrics["changed"],
            "letters": metrics["letters"], "learned_terms": learned, "reindexed": True,
            "graph_refresh_scheduled": True}


@router.post("/documents/{document_id}/reprocess", status_code=202)
def reprocess_document(case_id: UUID, document_id: UUID, request: Request,
                       ocr_mode: str | None = None,
                       p: Principal = Depends(current_principal)):
    """Re-ejecuta el pipeline OCR + indexación de un documento ya subido.

    `ocr_mode` puede ser:
    - None: usa el modo guardado en el documento (o el default del sistema)
    - "basico": OCR local (Tesseract/Docling)
    - "document_ai": Google Document AI
    """
    case_access(p, case_id, "document.upload")
    s = get_settings()
    if ocr_mode and ocr_mode not in ("basico", "document_ai"):
        raise AppError("VALIDATION_ERROR", 422, [{"field": "ocr_mode", "type": "enum", "allowed": ["basico", "document_ai"]}])
    with tx(p.org_id, p.user_id) as c:
        # Actualizar modo OCR si se especificó uno nuevo.
        if ocr_mode:
            one(c, """UPDATE documents SET ocr_mode = :m, processing_status = 'OCR_PENDING'
                      WHERE id = :d AND case_id = :c RETURNING id""",
                m=ocr_mode, d=str(document_id), c=str(case_id))
        else:
            one(c, """UPDATE documents SET processing_status = 'OCR_PENDING'
                      WHERE id = :d AND case_id = :c RETURNING id""",
                d=str(document_id), c=str(case_id))
        doc = one(c, "SELECT id FROM documents WHERE id = :d AND case_id = :c",
                  d=str(document_id), c=str(case_id))
        if not doc:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="document.reprocess_requested",
                     entity_type="document", entity_id=str(document_id),
                     after={"ocr_mode": ocr_mode}, request=request)
        job = create_job(c, org_id=p.org_id, case_id=str(case_id), job_type="file_ingest",
                         input_ids=[str(document_id)], actor_id=str(p.user_id),
                         key_parts=["file_ingest", str(case_id), str(document_id), "reprocess",
                                    int(time.time() // 60)],
                         pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
    enqueue_if_pending(job, p.org_id, p.user_id)
    return {"document_id": str(document_id), "job_id": str(job["id"]), "status": "QUEUED", "ocr_mode": ocr_mode}


@router.get("/media")
def list_media(case_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "media.read")
    with tx(p.org_id, p.user_id) as c:
        return rows(c, """SELECT m.id, m.filename, m.title, m.media_type, m.mime_type, m.size_bytes, m.duration_ms,
                          m.processing_status, m.created_at,
                          (SELECT count(*) FROM transcript_segments s WHERE s.media_id = m.id) AS segments
                          FROM media m WHERE m.case_id = :c ORDER BY m.created_at""", c=str(case_id))


@router.get("/media/{media_id}/segments")
def list_segments(case_id: UUID, media_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "media.read")
    with tx(p.org_id, p.user_id) as c:
        media = one(c, "SELECT filename, title FROM media WHERE id = :m AND case_id = :c", m=str(media_id), c=str(case_id))
        if not media:
            raise AppError("NOT_FOUND", 404)
        segs = rows(c, """SELECT s.id, s.start_ms, s.end_ms, s.text, s.confidence, s.needs_review, s.language,
                          s.speaker_id, sp.label AS speaker_label, sp.display_name AS speaker_name, sp.resolved_party_id
                          FROM transcript_segments s LEFT JOIN speakers sp ON sp.id = s.speaker_id
                          WHERE s.media_id = :m ORDER BY s.start_ms""", m=str(media_id))
        # Hablantes detectados en ESTE media (para los tags de edición general del nombre).
        spks = rows(c, """SELECT sp.id, sp.label, sp.display_name, sp.speaker_role, sp.resolved_party_id,
                                 sp.resolution_status, sp.version
                          FROM speakers sp
                          WHERE sp.case_id = :c AND sp.label <> 'UNKNOWN'
                            AND EXISTS (SELECT 1 FROM transcript_segments s
                                        WHERE s.speaker_id = sp.id AND s.media_id = :m)
                          ORDER BY sp.label""", c=str(case_id), m=str(media_id))
        unknown = one(c, "SELECT id FROM speakers WHERE case_id = :c AND media_id = :m AND label = 'UNKNOWN' LIMIT 1",
                      c=str(case_id), m=str(media_id))
    return {"media_id": str(media_id), "filename": media["filename"], "title": media["title"],
            "segments": segs, "speakers": spks,
            "unknown_speaker_id": str(unknown["id"]) if unknown else None}


@router.patch("/media/{media_id}/segments/{segment_id}")
def update_segment(case_id: UUID, media_id: UUID, segment_id: UUID, body: TranscriptSegmentPatch, request: Request,
                   p: Principal = Depends(current_principal)):
    """Corrige la transcripción de un segmento: el texto y/o quién lo dijo.

    Mejora el lexicón cuando cambia el texto y reindexa el media (los metadatos de
    los chunks incluyen al hablante)."""
    case_access(p, case_id, "media.upload")
    with tx(p.org_id, p.user_id) as c:
        sets = ["needs_review = false"]
        params: dict[str, object] = {"s": str(segment_id), "m": str(media_id)}
        if body.text is not None:
            sets += ["text = :t", "confidence = 1.0"]
            params["t"] = body.text
        speaker_set = "speaker_id" in body.model_fields_set
        if speaker_set:
            if body.speaker_id is not None:
                spk = one(c, "SELECT id FROM speakers WHERE id = :spk AND case_id = :c",
                          spk=str(body.speaker_id), c=str(case_id))
                if not spk:
                    raise AppError("SPEAKER_NOT_FOUND", 404)
            sets.append("speaker_id = :spk")
            params["spk"] = str(body.speaker_id) if body.speaker_id is not None else None
        seg = one(c, f"""UPDATE transcript_segments SET {', '.join(sets)}
                        WHERE id = :s AND media_id = :m RETURNING id""", **params)
        if not seg:
            raise AppError("NOT_FOUND", 404)
        learned: list[str] = []
        if body.text is not None:
            learned = ocr_lexicon.learn_terms(c, p.org_id, body.text)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="media.segment_corrected", entity_type="media",
                     entity_id=str(media_id),
                     after={"segment": str(segment_id), "text": body.text,
                            "speaker_id": params.get("spk") if speaker_set else None,
                            "learned_terms": learned}, request=request)
    with tx(p.org_id, p.user_id) as c:
        try:
            indexing.index_media(c, p.org_id, str(case_id), str(media_id), actor_id=p.user_id)
        except Exception:
            log.exception("no se pudo reindexar el media %s tras corregir el segmento", media_id)
    # El grafo del caso se reconstruye (deduplicado por ventana) con el nombre/cita ya corregidos.
    try:
        enqueue_graph_refresh(str(p.org_id), str(case_id), str(p.user_id), correction=True)
    except Exception:
        log.exception("no se pudo encolar graph_build tras corregir el segmento (caso %s)", case_id)
    return {"segment_id": str(segment_id), "learned_terms": learned, "reindexed": True,
            "graph_refresh_scheduled": True}


@router.get("/documents/{document_id}/pages/{page_number}/image")
def page_image(case_id: UUID, document_id: UUID, page_number: int, p: Principal = Depends(download_principal)):
    """Imagen de una página: usa la rasterizada si existe; si no, la renderiza del PDF original."""
    case_access(p, case_id, "document.read")
    with tx(p.org_id, p.user_id) as c:
        page = one(c, """SELECT p.image_uri, d.storage_uri, d.mime_type FROM document_pages p
                         JOIN documents d ON d.id = p.document_id
                         WHERE d.case_id = :c AND p.document_id = :d AND p.page_number = :n""",
                   c=str(case_id), d=str(document_id), n=page_number)
    if not page:
        raise AppError("DOCUMENT_NOT_FOUND", 404)
    if page["image_uri"]:
        try:
            return Response(storage().get(key_from_uri(page["image_uri"])), media_type="image/png")
        except Exception:
            log.warning("imagen rasterizada no disponible para %s p%s; se renderiza del original",
                        document_id, page_number)
    try:
        import fitz
        raw = storage().get(key_from_uri(page["storage_uri"]))
        ftype = "pdf" if (page["mime_type"] or "").endswith("pdf") else "png"
        with fitz.open(stream=raw, filetype=ftype) as doc:
            if page_number < 1 or page_number > doc.page_count:
                raise AppError("DOCUMENT_NOT_FOUND", 404)
            pix = doc.load_page(page_number - 1).get_pixmap(dpi=150)
            data = pix.tobytes("png")
    except AppError:
        raise
    except Exception:
        raise AppError("DOCUMENT_NOT_FOUND", 404) from None  # original no disponible en storage
    return Response(data, media_type="image/png")


@router.get("/media/{media_id}/download")
def media_download(case_id: UUID, media_id: UUID, request: Request, p: Principal = Depends(download_principal)):
    """Entrega el video/audio. Soporta HTTP Range para streaming (el navegador
    reproduce y salta de minuto sin descargar el archivo completo)."""
    case_access(p, case_id, "media.read")
    with tx(p.org_id, p.user_id) as c:
        m = one(c, "SELECT id, storage_uri, mime_type, filename, size_bytes FROM media WHERE id = :m AND case_id = :c",
                m=str(media_id), c=str(case_id))
        if not m:
            raise AppError("NOT_FOUND", 404)
        key = key_from_uri(m["storage_uri"])
        total = int(m["size_bytes"] or 0)
        rng = request.headers.get("range")
        if not rng:
            audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="media.downloaded", entity_type="media",
                         entity_id=str(media_id), request=request)
    safe = m["filename"].encode("ascii", "ignore").decode().replace('"', "") or "media"
    mime = m["mime_type"] or "application/octet-stream"
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": f'inline; filename="{safe}"'}
    if rng and total:
        start, end = _parse_range(rng, total)
        if start is None:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        # Entrega por trozos: si el rango es abierto (bytes=0-) o enorme, se limita
        # para no bajar el archivo completo en una sola respuesta (streaming fluido).
        end = min(end, start + _MAX_RANGE_CHUNK - 1)
        data = storage().get_range(key, start, end)
        return Response(data, status_code=206, media_type=mime, headers={
            **headers, "Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(len(data))})
    data = storage().get(key)
    return Response(data, media_type=mime, headers={**headers, "Content-Length": str(len(data))})


_MAX_RANGE_CHUNK = 4 * 1024 * 1024  # 4 MB por respuesta (el navegador pide el resto)


def _parse_range(header: str, total: int) -> tuple[int | None, int | None]:
    """Parsea 'bytes=inicio-fin' → (inicio, fin) inclusivos; (None, None) si no es válido."""
    try:
        spec = header.split("=", 1)[1]
        first, _, last = spec.partition("-")
        if first:
            start = int(first)
            end = int(last) if last else total - 1
        else:  # sufijo: últimos N bytes
            n = int(last)
            start, end = max(0, total - n), total - 1
        if start > end or start >= total:
            return None, None
        return start, min(end, total - 1)
    except (ValueError, IndexError):
        return None, None


@router.get("/documents/{document_id}/download")
def download(case_id: UUID, document_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "document.download")
    with tx(p.org_id, p.user_id) as c:
        d = one(c, "SELECT id, storage_uri, sha256, mime_type, filename FROM documents WHERE id = :d AND case_id = :c",
                d=str(document_id), c=str(case_id))
        if not d:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        data = storage().get(key_from_uri(d["storage_uri"]))
        # SSD §26: demostrar que lo entregado corresponde al original registrado
        if hashlib.sha256(data).hexdigest() != d["sha256"]:
            audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="document.integrity_failed", entity_type="document",
                         entity_id=str(document_id), request=request)
            raise AppError("INTERNAL_ERROR", 500)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="document.downloaded", entity_type="document",
                     entity_id=str(document_id), request=request)
    safe = d["filename"].encode("ascii", "ignore").decode().replace('"', "") or "document"
    return Response(data, media_type=d["mime_type"], headers={
        "Content-Disposition": f'attachment; filename="{safe}"', "X-Content-SHA256": d["sha256"]})


@router.delete("/documents/{document_id}")
def delete_document(case_id: UUID, document_id: UUID, request: Request,
                    p: Principal = Depends(require_org("evidence.deletion_request"))):
    """Elimina el documento INMEDIATAMENTE: BD + pgvector + grafo + storage.

    Respeta el legal hold (409 si el proceso tiene medida de conservación)."""
    case = case_access(p, case_id, "case.read")
    if case["legal_hold"]:
        raise AppError("LEGAL_HOLD_ACTIVE", 409, {"detail": "El proceso tiene medida de conservación (legal hold)."})
    with tx(p.org_id, p.user_id) as c:
        try:
            info = file_delete.delete_document(c, p.org_id, str(case_id), str(document_id))
        except ValueError:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="document.deleted", entity_type="document",
                     entity_id=str(document_id), request=request)
    file_delete.delete_storage(info)
    enqueue_graph_refresh(p.org_id, str(case_id), p.user_id, correction=True)  # grafo al día
    return {"document_id": str(document_id), "status": "deleted"}


@router.delete("/media/{media_id}")
def delete_media(case_id: UUID, media_id: UUID, request: Request,
                 p: Principal = Depends(require_org("evidence.deletion_request"))):
    """Elimina un video/audio INMEDIATAMENTE: segmentos + chunks + citas + storage + grafo."""
    case = case_access(p, case_id, "case.read")
    if case["legal_hold"]:
        raise AppError("LEGAL_HOLD_ACTIVE", 409, {"detail": "El proceso tiene medida de conservación (legal hold)."})
    with tx(p.org_id, p.user_id) as c:
        try:
            info = file_delete.delete_media(c, p.org_id, str(case_id), str(media_id))
        except ValueError:
            raise AppError("MEDIA_NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="media.deleted", entity_type="media",
                     entity_id=str(media_id), request=request)
    file_delete.delete_storage(info)
    enqueue_graph_refresh(p.org_id, str(case_id), p.user_id, correction=True)  # grafo al día
    return {"media_id": str(media_id), "status": "deleted"}


@router.post("/documents/{document_id}/deletion-request", status_code=202)
def deletion_request(case_id: UUID, document_id: UUID, body: DeletionRequestIn, request: Request,
                     p: Principal = Depends(require_org("evidence.deletion_request"))):
    case = case_access(p, case_id, "case.read")
    if case["legal_hold"]:
        raise AppError("LEGAL_HOLD_ACTIVE", 409)
    with tx(p.org_id, p.user_id) as c:
        r = c.execute(text("UPDATE documents SET deletion_requested_at = now(), deletion_requested_by = :u, deletion_reason = :r "
                           "WHERE id = :d AND case_id = :c"), {"u": p.user_id, "r": body.reason, "d": str(document_id), "c": str(case_id)})
        if r.rowcount == 0:
            raise AppError("DOCUMENT_NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="document.deletion_requested", entity_type="document",
                     entity_id=str(document_id), after={"reason": body.reason}, request=request)
    return {"document_id": str(document_id), "status": "deletion_requested"}


@router.post("/media/{media_id}/deletion-request", status_code=202)
def media_deletion_request(case_id: UUID, media_id: UUID, body: DeletionRequestIn, request: Request,
                           p: Principal = Depends(require_org("evidence.deletion_request"))):
    """Solicita la eliminación de un video/audio (no se borra directo; respeta legal hold)."""
    case = case_access(p, case_id, "case.read")
    if case["legal_hold"]:
        raise AppError("LEGAL_HOLD_ACTIVE", 409)
    with tx(p.org_id, p.user_id) as c:
        r = c.execute(text("UPDATE media SET deletion_requested_at = now(), deletion_requested_by = :u, deletion_reason = :r "
                           "WHERE id = :m AND case_id = :c"), {"u": p.user_id, "r": body.reason, "m": str(media_id), "c": str(case_id)})
        if r.rowcount == 0:
            raise AppError("MEDIA_NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="media.deletion_requested", entity_type="media",
                     entity_id=str(media_id), after={"reason": body.reason}, request=request)
    return {"media_id": str(media_id), "status": "deletion_requested"}
