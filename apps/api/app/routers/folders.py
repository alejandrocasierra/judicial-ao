"""Módulo Procesos: árbol de carpetas y archivos por expediente.

Los PDF y videos subidos desde aquí se registran en documents/media (pipeline
OCR/ASR) enlazados a su carpeta; el resto de tipos (xlsx, docx, imágenes, svg)
viven en case_files como archivos planos del proceso.
"""
from __future__ import annotations

import hashlib
import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.schemas import CaseFilePatch, FolderCreate, FolderPatch, UploadCompleteIn, UploadPresignIn
from app.security.deps import Principal, case_access, current_principal
from app.services import audit, files, ratelimit
from app.services.storage import key_from_uri, original_key, storage
from app.workers.dispatcher import create_job, enqueue_if_pending

router = APIRouter(prefix="/cases/{case_id}", tags=["folders"])
log = logging.getLogger(__name__)

# Tipos permitidos en el gestor de archivos del proceso.
ALLOWED_EXTS = {"xlsx", "docx", "pdf", "jpg", "jpeg", "png", "svg", "mp4"}
DOC_ROUTE_EXTS = {"pdf"}          # -> documents (pipeline OCR)
MEDIA_ROUTE_EXTS = {"mp4"}        # -> media (pipeline ASR)
EXTRA_MIME = {"svg": "image/svg+xml"}

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _validate_folder_name(name: str) -> str:
    name = name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise AppError("FOLDER_NAME_INVALID", 422)
    return name


def _folder_or_404(c, case_id: UUID, folder_id: str) -> dict:
    f = one(c, "SELECT id, parent_id, name FROM case_folders WHERE id = :f AND case_id = :c",
            f=folder_id, c=str(case_id))
    if not f:
        raise AppError("FOLDER_NOT_FOUND", 404)
    return f


def _folder_param(folder_id: str | None) -> str | None:
    """Normaliza el query param: None/''/'root' => raíz (NULL)."""
    if folder_id in (None, "", "root"):
        return None
    if not _UUID_RE.match(folder_id):
        raise AppError("FOLDER_NOT_FOUND", 404)
    return folder_id


def _folder_paths(c, case_id: str) -> dict[str, str]:
    """Mapa {folder_id: 'A / B / C'} de todo el árbol de carpetas del expediente."""
    fs = rows(c, "SELECT id, parent_id, name FROM case_folders WHERE case_id = :c", c=case_id)
    by_id = {str(f["id"]): f for f in fs}
    out: dict[str, str] = {}
    for fid in by_id:
        parts: list[str] = []
        cur: str | None = fid
        seen: set[str] = set()
        while cur and cur in by_id and cur not in seen:
            seen.add(cur)
            parts.append(by_id[cur]["name"])
            parent = by_id[cur]["parent_id"]
            cur = str(parent) if parent else None
        out[fid] = " / ".join(reversed(parts))
    return out


# ---------------------------------------------------------------- carpetas


@router.get("/folders")
def list_folders(case_id: UUID, p: Principal = Depends(current_principal)):
    """Árbol de carpetas con conteos RECURSIVOS (incluyen las subcarpetas).

    - `subfolders`: subcarpetas directas.
    - `files`: archivos de la carpeta y toda su descendencia, SIN contar videos
      (case_files + documents). Incluye subcarpetas para que una carpeta contenedora
      no aparezca con 0 archivos.
    - `videos`: videos (.mp4 -> tabla media) de la carpeta y toda su descendencia.
    """
    case_access(p, case_id, "document.read")
    with tx(p.org_id, p.user_id) as c:
        return rows(c, """
            WITH RECURSIVE descendants AS (
                SELECT f.id AS root_id, f.id AS folder_id
                  FROM case_folders f WHERE f.case_id = :c
                UNION ALL
                SELECT d.root_id, ch.id
                  FROM descendants d
                  JOIN case_folders ch ON ch.parent_id = d.folder_id
                 WHERE ch.case_id = :c
            ),
            entries AS (
                SELECT folder_id, FALSE AS is_video FROM case_files WHERE case_id = :c
                UNION ALL
                SELECT folder_id, FALSE FROM documents WHERE case_id = :c
                UNION ALL
                SELECT folder_id, TRUE FROM media WHERE case_id = :c
            ),
            counts AS (
                SELECT d.root_id,
                       count(*) FILTER (WHERE NOT e.is_video) AS files,
                       count(*) FILTER (WHERE e.is_video) AS videos
                  FROM descendants d
                  JOIN entries e ON e.folder_id = d.folder_id
                 GROUP BY d.root_id
            )
            SELECT f.id, f.parent_id, f.name, f.created_at,
                (SELECT count(*) FROM case_folders ch WHERE ch.parent_id = f.id) AS subfolders,
                COALESCE(ct.files, 0) AS files,
                COALESCE(ct.videos, 0) AS videos
              FROM case_folders f
              LEFT JOIN counts ct ON ct.root_id = f.id
             WHERE f.case_id = :c
             ORDER BY f.name""", c=str(case_id))


@router.post("/folders", status_code=201)
def create_folder(case_id: UUID, body: FolderCreate, request: Request, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.write")
    name = _validate_folder_name(body.name)
    try:
        with tx(p.org_id, p.user_id) as c:
            if body.parent_id:
                _folder_or_404(c, case_id, str(body.parent_id))
            folder = one(c, """INSERT INTO case_folders (organization_id, case_id, parent_id, name, created_by)
                VALUES (:o,:c,:parent,:n,:u) RETURNING id, parent_id, name, created_at""",
                         o=p.org_id, c=str(case_id), parent=str(body.parent_id) if body.parent_id else None,
                         n=name, u=p.user_id)
            audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="folder.created", entity_type="folder",
                         entity_id=str(folder["id"]), after={"name": name, "parent_id": str(body.parent_id or "")},
                         request=request)
    except IntegrityError:
        raise AppError("FOLDER_DUPLICATE", 409) from None
    return folder


@router.patch("/folders/{folder_id}")
def rename_folder(case_id: UUID, folder_id: UUID, body: FolderPatch, request: Request,
                  p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.write")
    name = _validate_folder_name(body.name)
    try:
        with tx(p.org_id, p.user_id) as c:
            old = _folder_or_404(c, case_id, str(folder_id))
            upd = one(c, """UPDATE case_folders SET name = :n, updated_at = now()
                            WHERE id = :f AND case_id = :c RETURNING id, parent_id, name, created_at""",
                      n=name, f=str(folder_id), c=str(case_id))
            audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="folder.renamed", entity_type="folder",
                         entity_id=str(folder_id), before={"name": old["name"]}, after={"name": name}, request=request)
    except IntegrityError:
        raise AppError("FOLDER_DUPLICATE", 409) from None
    return upd


@router.delete("/folders/{folder_id}")
def delete_folder(case_id: UUID, folder_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    """Sólo elimina carpetas vacías (sin subcarpetas ni archivos de ningún tipo)."""
    case_access(p, case_id, "case.write")
    with tx(p.org_id, p.user_id) as c:
        folder = _folder_or_404(c, case_id, str(folder_id))
        children = one(c, """SELECT
                (SELECT count(*) FROM case_folders ch WHERE ch.parent_id = :f) AS subfolders,
                (SELECT count(*) FROM case_files cf WHERE cf.folder_id = :f)
              + (SELECT count(*) FROM documents d WHERE d.folder_id = :f)
              + (SELECT count(*) FROM media m WHERE m.folder_id = :f) AS files""", f=str(folder_id))
        if children["subfolders"] or children["files"]:
            raise AppError("FOLDER_NOT_EMPTY", 409, children)
        c.execute(text("DELETE FROM case_folders WHERE id = :f AND case_id = :c"),
                  {"f": str(folder_id), "c": str(case_id)})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="folder.deleted", entity_type="folder",
                     entity_id=str(folder_id), before={"name": folder["name"]}, request=request)
    return {"folder_id": str(folder_id), "deleted": True}


# ---------------------------------------------------------------- archivos


@router.get("/files")
def list_files(case_id: UUID, folder_id: str | None = Query(default=None),
               p: Principal = Depends(current_principal)):
    """Archivos de una carpeta: union de case_files + documents (PDF) + media (videos)."""
    case_access(p, case_id, "document.read")
    fid = _folder_param(folder_id)
    with tx(p.org_id, p.user_id) as c:
        if fid:
            _folder_or_404(c, case_id, fid)
        items = rows(c, """SELECT * FROM (
                SELECT 'file' AS kind, cf.id, cf.filename, NULL AS title, cf.mime_type, cf.size_bytes,
                       NULL AS page_count, NULL AS processing_status, cf.created_at
                  FROM case_files cf WHERE cf.case_id = :c AND cf.folder_id IS NOT DISTINCT FROM :f
                UNION ALL
                SELECT 'document', d.id, d.filename, NULL, d.mime_type, d.size_bytes, d.page_count,
                       d.processing_status, d.created_at
                  FROM documents d WHERE d.case_id = :c AND d.folder_id IS NOT DISTINCT FROM :f
                UNION ALL
                SELECT 'media', m.id, m.filename, m.title, m.mime_type, m.size_bytes, NULL,
                       m.processing_status, m.created_at
                  FROM media m WHERE m.case_id = :c AND m.folder_id IS NOT DISTINCT FROM :f
            ) u ORDER BY u.filename""", c=str(case_id), f=fid)
    return {"folder_id": fid, "items": items}


@router.get("/search")
def search_process(case_id: UUID, q: str = Query(..., min_length=1), limit: int = 100,
                   p: Principal = Depends(current_principal)):
    """Busca carpetas, archivos, documentos (PDF) y videos en TODO el proceso.

    Incluye subcarpetas: cada resultado indica su `folder_path`. La búsqueda es por
    nombre (sin distinguir mayúsculas)."""
    case_access(p, case_id, "document.read")
    term = q.strip()
    if not term:
        return {"query": q, "folders": [], "items": []}
    like = f"%{term}%"
    with tx(p.org_id, p.user_id) as c:
        paths = _folder_paths(c, str(case_id))
        folders = rows(c, """SELECT id, name, parent_id FROM case_folders
                             WHERE case_id = :c AND name ILIKE :p
                             ORDER BY name LIMIT :l""", c=str(case_id), p=like, l=limit)
        for f in folders:
            f["path"] = paths.get(str(f["id"]), f["name"])
        items = rows(c, """SELECT * FROM (
                SELECT 'file' AS kind, cf.id, cf.filename, NULL::text AS title, cf.mime_type, cf.size_bytes,
                       NULL::int AS page_count, NULL::text AS processing_status, cf.folder_id, cf.created_at
                  FROM case_files cf WHERE cf.case_id = :c AND cf.filename ILIKE :p
                UNION ALL
                SELECT 'document', d.id, d.filename, NULL, d.mime_type, d.size_bytes, d.page_count,
                       d.processing_status, d.folder_id, d.created_at
                  FROM documents d WHERE d.case_id = :c AND d.filename ILIKE :p
                UNION ALL
                SELECT 'media', m.id, m.filename, m.title, m.mime_type, m.size_bytes, NULL,
                       m.processing_status, m.folder_id, m.created_at
                  FROM media m WHERE m.case_id = :c AND (m.filename ILIKE :p OR m.title ILIKE :p)
            ) u ORDER BY u.filename LIMIT :l""", c=str(case_id), p=like, l=limit)
        for it in items:
            it["folder_path"] = paths.get(str(it["folder_id"])) if it["folder_id"] else "Raíz del proceso"
    return {"query": term, "folders": folders, "items": items}


def _sniff_svg(data: bytes) -> bool:
    head = data[:4096].lstrip(b"\xef\xbb\xbf\x00 \t\r\n").lower()
    return head.startswith(b"<?xml") or head.startswith(b"<svg")


def _validate_upload(data: bytes, filename: str, declared: str | None) -> tuple[str, str]:
    """Devuelve (ext_real, mime). El SVG no tiene firma binaria: se valida como texto."""
    ext = files.extension(filename)
    if ext not in ALLOWED_EXTS:
        raise AppError("UPLOAD_TYPE_NOT_ALLOWED", 415)
    if ext == "svg":
        if not _sniff_svg(data):
            raise AppError("UPLOAD_CONTENT_MISMATCH", 415)
        real = "svg"
    else:
        real = files.sniff(data)
        if real != ext or (declared and declared not in (files.MIME[real], "application/octet-stream")):
            raise AppError("UPLOAD_CONTENT_MISMATCH", 415)
    try:
        files.scan(data)
    except files.MalwareFound:
        raise AppError("UPLOAD_MALWARE_DETECTED", 422) from None
    return real, EXTRA_MIME.get(real) or files.MIME[real]


def _already_registered(c, case_id: UUID, sha: str, filename: str) -> str | None:
    """'document' | 'media' | 'file' si el mismo contenido Y nombre ya existe; None si no.

    El mismo contenido con OTRO nombre sí se permite (un expediente puede tener el
    mismo documento archivado bajo nombres distintos).
    """
    if one(c, "SELECT id FROM documents WHERE case_id = :c AND sha256 = :h AND filename = :f",
           c=str(case_id), h=sha, f=filename):
        return "document"
    if one(c, "SELECT id FROM media WHERE case_id = :c AND sha256 = :h AND filename = :f",
           c=str(case_id), h=sha, f=filename):
        return "media"
    if one(c, "SELECT id FROM case_files WHERE case_id = :c AND sha256 = :h AND filename = :f",
           c=str(case_id), h=sha, f=filename):
        return "file"
    return None


@router.post("/files", status_code=201)
def upload_files(case_id: UUID, request: Request,
                 uploads: list[UploadFile] | None = File(default=None),
                 files_files: list[UploadFile] | None = File(default=None, alias="files"),
                 file_single: list[UploadFile] | None = File(default=None, alias="file"),
                 folder_id: str | None = Form(default=None),
                 ocr_mode: str | None = Form(default=None),
                 p: Principal = Depends(current_principal)):
    """Subida de archivos. `ocr_mode` controla si se procesa automáticamente:
    - None o "none": sube sin procesar (el usuario decide después)
    - "basico": OCR local (Tesseract/Docling)
    - "document_ai": Google Document AI
    """
    s = get_settings()
    # Compatibilidad de campo multipart: clientes nuevos usan `uploads`; clientes
    # antiguos (bundles en caché) usan `files` o `file`. Aceptamos todos.
    batch: list[UploadFile] = [*(uploads or []), *(files_files or []), *(file_single or [])]
    if not batch:
        raise AppError("VALIDATION_ERROR", 422, [{"field": "uploads", "type": "missing"}])
    fid = _folder_param(folder_id)
    # Normalizar modo OCR: None/"none" -> no procesar.
    ocr_mode_normalized = None if ocr_mode in (None, "", "none") else ocr_mode
    if ocr_mode_normalized not in (None, "basico", "document_ai"):
        raise AppError("VALIDATION_ERROR", 422, [{"field": "ocr_mode", "type": "enum", "allowed": ["none", "basico", "document_ai"]}])
    ratelimit.check("upload", p.user_id)
    ratelimit.check_org("upload", p.org_id)

    # Permiso según el tipo más exigente del lote.
    exts = {files.extension(u.filename or "") for u in batch}
    case_access(p, case_id, "media.upload" if exts & MEDIA_ROUTE_EXTS else "document.upload")

    results: list[dict] = []
    ingest_jobs: list[dict] = []  # jobs file_ingest a encolar tras el commit de cada archivo
    for upload in batch:
        filename = files.sanitize_filename(upload.filename or "")
        try:
            is_media = files.extension(filename) in MEDIA_ROUTE_EXTS
            limit = s.UPLOAD_MAX_BYTES_MEDIA if is_media else s.UPLOAD_MAX_BYTES_DOCUMENT
            buf, total = bytearray(), 0
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise AppError("UPLOAD_TOO_LARGE", 413)
                buf.extend(chunk)
            if total == 0:
                raise AppError("UPLOAD_EMPTY", 422)
            data = bytes(buf)
            real, mime = _validate_upload(data, filename, upload.content_type)
            sha = hashlib.sha256(data).hexdigest()
            with tx(p.org_id, p.user_id) as c:
                if fid:
                    _folder_or_404(c, case_id, fid)
                existing = _already_registered(c, case_id, sha, filename)
                if existing:
                    raise AppError("DOCUMENT_DUPLICATE", 409, {"existing_kind": existing})
                if real in DOC_ROUTE_EXTS:
                    uri = storage().put(original_key(str(case_id), sha, "document"), data)
                    row = one(c, """INSERT INTO documents (organization_id, case_id, folder_id, storage_uri, sha256,
                                        size_bytes, mime_type, filename, uploaded_by, ocr_mode)
                        VALUES (:o,:c,:f,:u,:h,:sz,:m,:fn,:by,:ocr_mode)
                        RETURNING id, processing_status""",
                              o=p.org_id, c=str(case_id), f=fid, u=uri, h=sha, sz=len(data), m=mime, fn=filename,
                              by=p.user_id, ocr_mode=ocr_mode_normalized)
                    kind = "document"
                elif real in MEDIA_ROUTE_EXTS:
                    uri = storage().put(original_key(str(case_id), sha, "media"), data)
                    row = one(c, """INSERT INTO media (organization_id, case_id, folder_id, storage_uri, sha256,
                                        size_bytes, mime_type, filename, title, media_type, uploaded_by, asr_mode)
                        VALUES (:o,:c,:f,:u,:h,:sz,:m,:fn,:t,'video',:by,:asr_mode)
                        RETURNING id, processing_status""",
                              o=p.org_id, c=str(case_id), f=fid, u=uri, h=sha, sz=len(data), m=mime, fn=filename,
                              t=filename, by=p.user_id, asr_mode=ocr_mode_normalized)
                    kind = "media"
                else:
                    uri = storage().put(f"cases/{case_id}/files/{sha}", data)
                    row = one(c, """INSERT INTO case_files (organization_id, case_id, folder_id, storage_uri, sha256,
                                        size_bytes, mime_type, filename, uploaded_by)
                        VALUES (:o,:c,:f,:u,:h,:sz,:m,:fn,:by) RETURNING id""",
                              o=p.org_id, c=str(case_id), f=fid, u=uri, h=sha, sz=len(data), m=mime, fn=filename,
                              by=p.user_id)
                    kind = "file"
                audit.record(c, org_id=p.org_id, actor_id=p.user_id, action=f"{kind}.uploaded", entity_type=kind,
                             entity_id=str(row["id"]), after={"sha256": sha, "size": len(data), "folder_id": fid,
                                                            "ocr_mode": ocr_mode_normalized},
                             request=request)
                # PDF/MP4: procesamiento automático SÓLO si el usuario eligió un modo OCR.
                # XLSX: ingesta automática del índice (no requiere OCR de contenido).
                job = None
                if kind in ("document", "media") and ocr_mode_normalized:
                    job = create_job(c, org_id=p.org_id, case_id=str(case_id), job_type="file_ingest",
                                     input_ids=[str(row["id"])], actor_id=p.user_id,
                                     key_parts=["file_ingest", str(case_id), str(row["id"]), s.PIPELINE_VERSION],
                                     pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
                elif kind == "file" and real == "xlsx":
                    job = create_job(c, org_id=p.org_id, case_id=str(case_id), job_type="xlsx_ingest",
                                     input_ids=[str(row["id"])], actor_id=p.user_id,
                                     key_parts=["xlsx_ingest", str(case_id), str(row["id"]), s.PIPELINE_VERSION],
                                     pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
            if job is not None:
                ingest_jobs.append(job)
            results.append({"filename": filename, "status": "uploaded", "kind": kind, "id": str(row["id"]),
                            "ocr_mode": ocr_mode_normalized,
                            **({"processing": "QUEUED", "job_id": str(job["id"])} if job else {})})
        except AppError as e:
            results.append({"filename": filename, "status": "error", "code": e.code})
        except Exception:
            log.exception("error subiendo %s al caso %s", filename, case_id)
            results.append({"filename": filename, "status": "error", "code": "INTERNAL_ERROR"})
    # Tras los COMMIT de cada archivo: encolar los jobs de procesamiento (un broker
    # caído no rompe la subida; el sweeper re-encola los QUEUED huérfanos).
    for job in ingest_jobs:
        enqueue_if_pending(job, p.org_id, p.user_id)
    return {"results": results}


def _storage_key_and_kind(case_id: UUID, filename: str, sha256: str) -> tuple[str, str]:
    ext = files.extension(filename)
    if ext not in ALLOWED_EXTS:
        raise AppError("UPLOAD_TYPE_NOT_ALLOWED", 415)
    if ext in MEDIA_ROUTE_EXTS:
        return "media", original_key(str(case_id), sha256, "media")
    if ext in DOC_ROUTE_EXTS:
        return "document", original_key(str(case_id), sha256, "document")
    return "file", f"cases/{case_id}/files/{sha256}"


@router.post("/uploads/presign")
def presign_upload(case_id: UUID, body: UploadPresignIn, p: Principal = Depends(current_principal)):
    """URL prefirmada para subir un archivo GRANDE directo al storage (evita el proxy
    de Cloudflare, que limita a 100 MB). Si el storage es local, devuelve `mode: local`
    y el cliente debe usar la subida normal (multipart)."""
    s = get_settings()
    kind, key = _storage_key_and_kind(case_id, body.filename, body.sha256)
    case_access(p, case_id, "media.upload" if kind == "media" else "document.upload")
    ratelimit.check("upload", p.user_id)
    ratelimit.check_org("upload", p.org_id)
    limit = s.UPLOAD_MAX_BYTES_MEDIA if kind == "media" else s.UPLOAD_MAX_BYTES_DOCUMENT
    if body.size_bytes > limit:
        raise AppError("UPLOAD_TOO_LARGE", 413)
    ext = files.extension(body.filename)
    content_type = body.content_type or EXTRA_MIME.get(ext) or files.MIME.get(ext)
    url = storage().presign_put(key, content_type, 3600)
    if not url:
        return {"mode": "local", "kind": kind}
    return {"mode": "direct", "method": "PUT", "upload_url": url, "key": key, "kind": kind}


@router.post("/uploads/complete", status_code=201)
def complete_upload(case_id: UUID, body: UploadCompleteIn, request: Request,
                    p: Principal = Depends(current_principal)):
    """Registra un archivo ya subido por URL prefirmada (documents/media/case_files)
    y encola su procesamiento, igual que la subida normal."""
    s = get_settings()
    kind, key = _storage_key_and_kind(case_id, body.filename, body.sha256)
    case_access(p, case_id, "media.upload" if kind == "media" else "document.upload")
    ratelimit.check("upload", p.user_id)
    ratelimit.check_org("upload", p.org_id)
    limit = s.UPLOAD_MAX_BYTES_MEDIA if kind == "media" else s.UPLOAD_MAX_BYTES_DOCUMENT
    if body.size_bytes > limit:
        raise AppError("UPLOAD_TOO_LARGE", 413)
    st = storage()
    if getattr(st, "size", lambda _k: None)(key) is None:
        raise AppError("NOT_FOUND", 404)  # el objeto no llegó al storage

    ext = files.extension(body.filename)
    mime = EXTRA_MIME.get(ext) or files.MIME.get(ext) or body.mime_type or "application/octet-stream"
    fid = _folder_param(str(body.folder_id)) if body.folder_id else None
    with tx(p.org_id, p.user_id) as c:
        if fid:
            _folder_or_404(c, case_id, fid)
        existing = _already_registered(c, case_id, body.sha256, body.filename)
        if existing:
            raise AppError("DOCUMENT_DUPLICATE", 409, [{"existing_kind": existing}])
        if kind == "document":
            uri = f"{'s3' if s.STORAGE_BACKEND == 's3' else 'gs'}://{s.S3_BUCKET}/{key}"
            row = one(c, """INSERT INTO documents (organization_id, case_id, folder_id, storage_uri, sha256,
                                size_bytes, mime_type, filename, uploaded_by, ocr_mode)
                VALUES (:o,:c,:f,:u,:h,:sz,:m,:fn,:by,:ocr_mode) RETURNING id, processing_status""",
                      o=p.org_id, c=str(case_id), f=fid, u=uri, h=body.sha256, sz=body.size_bytes, m=mime,
                      fn=body.filename, by=p.user_id, ocr_mode=body.ocr_mode)
        elif kind == "media":
            uri = f"{'s3' if s.STORAGE_BACKEND == 's3' else 'gs'}://{s.S3_BUCKET}/{key}"
            row = one(c, """INSERT INTO media (organization_id, case_id, folder_id, storage_uri, sha256,
                                size_bytes, mime_type, filename, title, media_type, uploaded_by, asr_mode)
                VALUES (:o,:c,:f,:u,:h,:sz,:m,:fn,:t,'video',:by,:asr_mode) RETURNING id, processing_status""",
                      o=p.org_id, c=str(case_id), f=fid, u=uri, h=body.sha256, sz=body.size_bytes, m=mime,
                      fn=body.filename, t=body.filename, by=p.user_id, asr_mode=body.ocr_mode)
        else:
            uri = f"{'s3' if s.STORAGE_BACKEND == 's3' else 'gs'}://{s.S3_BUCKET}/{key}"
            row = one(c, """INSERT INTO case_files (organization_id, case_id, folder_id, storage_uri, sha256,
                                size_bytes, mime_type, filename, uploaded_by)
                VALUES (:o,:c,:f,:u,:h,:sz,:m,:fn,:by) RETURNING id""",
                      o=p.org_id, c=str(case_id), f=fid, u=uri, h=body.sha256, sz=body.size_bytes, m=mime,
                      fn=body.filename, by=p.user_id)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action=f"{kind}.uploaded", entity_type=kind,
                     entity_id=str(row["id"]),
                     after={"sha256": body.sha256, "size": body.size_bytes, "folder_id": fid,
                            "ocr_mode": body.ocr_mode, "via": "direct_upload"}, request=request)
        job = None
        if kind in ("document", "media") and body.ocr_mode:
            job = create_job(c, org_id=p.org_id, case_id=str(case_id), job_type="file_ingest",
                             input_ids=[str(row["id"])], actor_id=p.user_id,
                             key_parts=["file_ingest", str(case_id), str(row["id"]), s.PIPELINE_VERSION],
                             pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
        elif kind == "file" and ext == "xlsx":
            job = create_job(c, org_id=p.org_id, case_id=str(case_id), job_type="xlsx_ingest",
                             input_ids=[str(row["id"])], actor_id=p.user_id,
                             key_parts=["xlsx_ingest", str(case_id), str(row["id"]), s.PIPELINE_VERSION],
                             pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
    if job is not None:
        enqueue_if_pending(job, p.org_id, p.user_id)
    return {"filename": body.filename, "status": "uploaded", "kind": kind, "id": str(row["id"]),
            "ocr_mode": body.ocr_mode,
            **({"processing": "QUEUED", "job_id": str(job["id"])} if job else {})}


@router.get("/files/{file_id}/download")
def download_file(case_id: UUID, file_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "document.download")
    with tx(p.org_id, p.user_id) as c:
        f = one(c, "SELECT id, storage_uri, mime_type, filename FROM case_files WHERE id = :f AND case_id = :c",
                f=str(file_id), c=str(case_id))
        if not f:
            raise AppError("FILE_NOT_FOUND", 404)
        data = storage().get(key_from_uri(f["storage_uri"]))
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="file.downloaded", entity_type="file",
                     entity_id=str(file_id), request=request)
    safe = f["filename"].encode("ascii", "ignore").decode().replace('"', "") or "file"
    # attachment siempre: un SVG servido inline podría ejecutar scripts.
    return Response(data, media_type=f["mime_type"],
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})


@router.patch("/files/{file_id}")
def rename_file(case_id: UUID, file_id: UUID, body: CaseFilePatch, request: Request,
                p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.write")
    filename = files.sanitize_filename(body.filename)
    with tx(p.org_id, p.user_id) as c:
        upd = one(c, """UPDATE case_files SET filename = :n WHERE id = :f AND case_id = :c
                        RETURNING id, filename, mime_type, size_bytes, created_at""",
                  n=filename, f=str(file_id), c=str(case_id))
        if not upd:
            raise AppError("FILE_NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="file.renamed", entity_type="file",
                     entity_id=str(file_id), after={"filename": filename}, request=request)
    return upd


@router.delete("/files/{file_id}")
def delete_file(case_id: UUID, file_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    """Elimina el registro del archivo (el objeto en storage es write-once)."""
    case_access(p, case_id, "case.write")
    with tx(p.org_id, p.user_id) as c:
        f = one(c, "SELECT id, filename FROM case_files WHERE id = :f AND case_id = :c",
                f=str(file_id), c=str(case_id))
        if not f:
            raise AppError("FILE_NOT_FOUND", 404)
        c.execute(text("DELETE FROM case_files WHERE id = :f AND case_id = :c"),
                  {"f": str(file_id), "c": str(case_id)})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="file.deleted", entity_type="file",
                     entity_id=str(file_id), before={"filename": f["filename"]}, request=request)
    return {"file_id": str(file_id), "deleted": True}
