from __future__ import annotations

import hashlib
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.domain import states
from app.domain.jurisdiction import jurisdictions, validate_case_number
from app.schemas import (CaseCreate, CasePatch, LegalHoldIn, MemberIn, PartyBulkIn, ProcessIn, SpeakerCreate,
                         SpeakerMergeIn, SpeakerPatch, SpeakerRoleAssign)
from app.security import rbac
from app.security.deps import Principal, case_access, current_principal, require_org
from app.services import audit, export, indexing, party_extraction, procedural_graph, purge
from app.services import speakers as speakers_service
from app.services.case_tools import read as ct_read
from app.workers.dispatcher import enqueue_job
from app.workers.handlers.file_ingest import enqueue_graph_refresh

router = APIRouter(prefix="/cases", tags=["cases"])
log = logging.getLogger(__name__)
CASE_COLS = ("id, organization_id, external_reference, jurisdiction, court, chamber, case_number, title, status, "
             "retention_status, legal_hold, knowledge_version, language, version, created_at, updated_at")


@router.post("", status_code=201)
def create_case(body: CaseCreate, request: Request, p: Principal = Depends(require_org("case.create"))):
    s = get_settings()
    if body.jurisdiction not in jurisdictions():
        raise AppError("JURISDICTION_UNKNOWN", 422)
    if not validate_case_number(body.jurisdiction, body.case_number):
        raise AppError("CASE_NUMBER_INVALID", 422)
    try:
        with tx(p.org_id, p.user_id) as c:
            case = one(c, f"""INSERT INTO cases (organization_id, external_reference, jurisdiction, court, chamber, case_number,
                    title, language, max_processing_cost, max_llm_tokens, max_media_hours, created_by)
                  VALUES (:o,:ext,:j,:court,:ch,:num,:t,:lang,:mc,:mt,:mh,:u) RETURNING {CASE_COLS}""",
                       o=p.org_id, ext=body.external_reference, j=body.jurisdiction, court=body.court, ch=body.chamber,
                       num=body.case_number, t=body.title, lang=body.language, mc=s.CASE_MAX_PROCESSING_COST,
                       mt=s.CASE_MAX_LLM_TOKENS, mh=s.CASE_MAX_MEDIA_HOURS, u=p.user_id)
            c.execute(text("INSERT INTO case_members (case_id, user_id, organization_id, case_role) VALUES (:c,:u,:o,'OWNER')"),
                      {"c": case["id"], "u": p.user_id, "o": p.org_id})
            audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="case.created", entity_type="case",
                         entity_id=str(case["id"]), after={"case_number": body.case_number}, request=request)
    except IntegrityError:
        raise AppError("CASE_DUPLICATE", 409) from None
    return case


@router.get("")
def list_cases(p: Principal = Depends(current_principal)):
    with tx(p.org_id, p.user_id) as c:
        if rbac.case_permissions(p.org_role, None):  # ORG_ADMIN: todos los de su organización
            return rows(c, f"SELECT {CASE_COLS} FROM cases ORDER BY created_at DESC")
        return rows(c, f"SELECT {', '.join('c.' + x.strip() for x in CASE_COLS.split(','))}, m.case_role FROM cases c "
                       "JOIN case_members m ON m.case_id = c.id AND m.user_id = :u ORDER BY c.created_at DESC", u=p.user_id)


@router.get("/{case_id}")
def get_case(case_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    case = case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        stats = one(c, """SELECT (SELECT count(*) FROM documents WHERE case_id=:c) AS documents,
              (SELECT count(*) FROM document_pages p JOIN documents d ON d.id=p.document_id WHERE d.case_id=:c) AS pages,
              (SELECT count(*) FROM media WHERE case_id=:c) AS media,
              (SELECT count(*) FROM claims WHERE case_id=:c) AS claims,
              (SELECT count(*) FROM facts WHERE case_id=:c) AS facts,
              (SELECT count(*) FROM contradictions WHERE case_id=:c) AS contradictions,
              (SELECT count(*) FROM document_pages p JOIN documents d ON d.id=p.document_id WHERE d.case_id=:c AND p.needs_review)
                + (SELECT count(*) FROM transcript_segments s JOIN media m ON m.id=s.media_id WHERE m.case_id=:c AND s.needs_review)
                AS items_requiring_review""", c=str(case_id))
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="case.read", entity_type="case",
                     entity_id=str(case_id), request=request)
    out = {k: case[k] for k in [x.strip() for x in CASE_COLS.split(",")]}
    return {**out, "stats": stats}


@router.patch("/{case_id}")
def patch_case(case_id: UUID, body: CasePatch, request: Request, p: Principal = Depends(current_principal)):
    case = case_access(p, case_id, "case.write")
    if body.status and body.status != case["status"] and not states.can_transition(states.CASE_TRANSITIONS, case["status"], body.status):
        raise AppError("INVALID_STATE_TRANSITION", 409, {"from": case["status"], "to": body.status})
    changes = body.model_dump(exclude_none=True, exclude={"expected_version"})
    if not changes:
        return {k: case[k] for k in ("id", "version")}
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    with tx(p.org_id, p.user_id) as c:
        upd = one(c, f"UPDATE cases SET {sets}, version = version + 1 WHERE id = :id AND version = :v RETURNING {CASE_COLS}",
                  id=str(case_id), v=body.expected_version, **changes)
        if not upd:
            raise AppError("VERSION_CONFLICT", 409)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="case.updated", entity_type="case", entity_id=str(case_id),
                     before={k: case[k] for k in changes}, after=changes, request=request)
    return upd


@router.delete("/{case_id}")
def delete_case(case_id: UUID, p: Principal = Depends(current_principal)):
    """Purga total del proceso (módulo Procesos): archivos, carpetas, OCR/ASR,
    pgvector, knowledge graph y registros de BD. Sólo queda la tumba de auditoría
    (quién purgó qué radicado y cuándo). Requiere rol OWNER en el caso u ORG_ADMIN."""
    case = case_access(p, case_id, "case.members.manage")
    if case["legal_hold"]:
        raise AppError("LEGAL_HOLD_ACTIVE", 409)
    with tx(p.org_id, p.user_id) as c:
        stats = purge.purge_case(c, p.org_id, str(case_id), p.user_id, case)
    # Fuera de la transacción: objetos en storage (best-effort).
    objects = purge.purge_storage(str(case_id))
    return {"case_id": str(case_id), "purged": True, "rows_deleted": stats, "storage_objects_deleted": objects}


@router.post("/{case_id}/members", status_code=201)
def add_member(case_id: UUID, body: MemberIn, request: Request, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.members.manage")
    with tx(p.org_id, p.user_id) as c:
        if not one(c, "SELECT 1 FROM users WHERE id = :u AND is_active", u=str(body.user_id)):
            raise AppError("USER_NOT_FOUND", 404)  # RLS: usuarios de otra organización no existen aquí
        c.execute(text("INSERT INTO case_members (case_id, user_id, organization_id, case_role) VALUES (:c,:u,:o,:r) "
                       "ON CONFLICT (case_id, user_id) DO UPDATE SET case_role = EXCLUDED.case_role"),
                  {"c": str(case_id), "u": str(body.user_id), "o": p.org_id, "r": body.case_role})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="case.permissions_changed", entity_type="case",
                     entity_id=str(case_id), after={"user_id": str(body.user_id), "case_role": body.case_role}, request=request)
    return {"case_id": str(case_id), "user_id": str(body.user_id), "case_role": body.case_role}


@router.post("/{case_id}/legal-hold")
def legal_hold(case_id: UUID, body: LegalHoldIn, request: Request, p: Principal = Depends(require_org("legal_hold.manage"))):
    case = case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        c.execute(text("UPDATE cases SET legal_hold = :h, retention_status = :r, version = version + 1 WHERE id = :id"),
                  {"h": body.enabled, "r": "LEGAL_HOLD" if body.enabled else "ACTIVE", "id": str(case_id)})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="case.legal_hold_changed", entity_type="case",
                     entity_id=str(case_id), before={"legal_hold": case["legal_hold"]},
                     after={"legal_hold": body.enabled, "reason": body.reason}, request=request)
    return {"case_id": str(case_id), "legal_hold": body.enabled}


@router.post("/{case_id}/process", status_code=202)
def process(case_id: UUID, body: ProcessIn, request: Request, p: Principal = Depends(current_principal)):
    """Crea jobs idempotentes (SSD §24): misma entrada + versión => mismo job (REUSE)."""
    case_access(p, case_id, "job.run")
    s = get_settings()
    out = []
    with tx(p.org_id, p.user_id) as c:
        inputs = [str(r["id"]) for r in rows(c, "SELECT id FROM documents WHERE case_id = :c UNION ALL SELECT id FROM media WHERE case_id = :c ORDER BY 1", c=str(case_id))]
        shas = sorted(r["sha256"] for r in rows(c, "SELECT sha256 FROM documents WHERE case_id=:c UNION ALL SELECT sha256 FROM media WHERE case_id=:c", c=str(case_id)))
        for jt in body.job_types:
            key = hashlib.sha256(json.dumps([str(case_id), jt, shas, s.PIPELINE_VERSION, s.LLM_MODEL]).encode()).hexdigest()
            existing = one(c, "SELECT id, job_type, status FROM jobs WHERE idempotency_key = :k", k=key)
            if existing:
                out.append({**existing, "reused": True})
                continue
            # ON CONFLICT: dos requests concurrentes con la misma idempotency_key ya no
            # chocan contra el UNIQUE (500); el perdedor re-lee el job ganador y lo reusa.
            j = one(c, "INSERT INTO jobs (organization_id, case_id, job_type, input_ids, idempotency_key, "
                       "pipeline_version, model_version, created_by) "
                       "VALUES (:o,:c,:t,CAST(:i AS uuid[]),:k,:pv,:mv,:u) "
                       "ON CONFLICT (organization_id, idempotency_key) DO NOTHING RETURNING id, job_type, status",
                    o=p.org_id, c=str(case_id), t=jt, i="{" + ",".join(inputs) + "}", k=key, pv=s.PIPELINE_VERSION,
                    mv=s.LLM_MODEL, u=p.user_id)
            if j is None:
                j = one(c, "SELECT id, job_type, status FROM jobs WHERE idempotency_key = :k", k=key)
                out.append({**j, "reused": True})
                continue
            out.append({**j, "reused": False})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="case.processing_requested", entity_type="case",
                     entity_id=str(case_id), after={"job_types": body.job_types}, request=request)
    # Tras el COMMIT: encolar los jobs pendientes (nuevos o reutilizados aún QUEUED).
    # El executor es idempotente; un fallo de broker no rompe la petición (dispatcher).
    for j in out:
        if not j["reused"] or j["status"] == "QUEUED":
            enqueue_job(j["id"], p.org_id, p.user_id)
    return {"jobs": out}


@router.get("/processing/active")
def active_processing(p: Principal = Depends(current_principal)):
    """Todos los jobs activos de la organización (para el monitor global del header)."""
    with tx(p.org_id, p.user_id) as c:
        jobs = rows(c, """
            SELECT j.id, j.job_type, j.status, j.attempts, j.error_code,
                   j.input_ids, j.created_at, j.updated_at, j.case_id,
                   c.case_number, c.title AS case_title
            FROM jobs j
            JOIN cases c ON c.id = j.case_id
            WHERE j.organization_id = :o
              AND j.status IN ('QUEUED', 'RUNNING', 'RETRYING')
            ORDER BY j.created_at DESC
            LIMIT 20
        """, o=p.org_id)

        result = []
        for job in jobs:
            input_ids = [str(i) for i in (job["input_ids"] or [])]
            items = []

            if input_ids:
                # Documentos
                docs = rows(c, """
                    SELECT d.id, d.filename, d.processing_status, d.page_count, d.folder_id,
                           pr.pct AS live_pct, pr.detail AS live_detail,
                           (SELECT count(*) FROM document_pages p WHERE p.document_id = d.id) AS pages_done
                    FROM documents d
                    LEFT JOIN document_ocr_progress pr ON pr.document_id = d.id
                    WHERE d.id = ANY(:ids)
                """, ids=input_ids)
                for d in docs:
                    progress = 0
                    detail = None
                    status = d["processing_status"]
                    if d["processing_status"] in ("OCR_COMPLETE", "REVIEW_REQUIRED"):
                        progress = 100
                    elif d["live_pct"] is not None:
                        # Progreso en vivo: la transacción del pipeline aún no ha
                        # commiteado, así que el estado sigue siendo UPLOADED; usamos
                        # la tabla de progreso para mostrar "OCR en curso".
                        progress = min(99, int(d["live_pct"]))
                        detail = d["live_detail"]
                        status = "OCR_RUNNING"
                    elif d["processing_status"] in ("UPLOADED", "OCR_PENDING", "OCR_RUNNING"):
                        total = d["page_count"] or 0
                        done = d["pages_done"] or 0
                        progress = min(99, int(100 * done / total)) if total > 0 else 2
                    items.append({
                        "id": str(d["id"]), "filename": d["filename"],
                        "folder_path": _folder_path(c, str(d["folder_id"])) if d["folder_id"] else "Raíz del proceso",
                        "status": status, "progress": progress, "detail": detail,
                        "kind": "document",
                    })

                # Medias
                medias = rows(c, """
                    SELECT m.id, m.filename, m.processing_status
                    FROM media m WHERE m.id = ANY(:ids)
                """, ids=input_ids)
                for m in medias:
                    progress = 100 if m["processing_status"] in ("ASR_COMPLETE", "REVIEW_REQUIRED") else 50
                    items.append({
                        "id": str(m["id"]), "filename": m["filename"],
                        "status": m["processing_status"], "progress": progress,
                        "kind": "media",
                    })

            global_progress = round(sum(i["progress"] for i in items) / len(items)) if items else 0
            result.append({
                "id": str(job["id"]),
                "job_type": job["job_type"],
                "status": job["status"],
                "attempts": job["attempts"],
                "error_code": job["error_code"],
                "created_at": job["created_at"],
                "case_id": str(job["case_id"]),
                "case_number": job["case_number"],
                "case_title": job["case_title"],
                "progress": global_progress,
                "items": items,
            })

        return {"jobs": result, "active_count": len(result)}


@router.get("/{case_id}/processing")
def processing(case_id: UUID, p: Principal = Depends(current_principal)):
    case = case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        jobs = rows(c, "SELECT id, job_type, status, attempts, error_code, created_at, updated_at FROM jobs WHERE case_id=:c ORDER BY created_at", c=str(case_id))
    return {"case_status": case["status"], "jobs": jobs}


@router.get("/{case_id}/processing/ocr-status")
def ocr_processing_status(case_id: UUID, p: Principal = Depends(current_principal)):
    """Estado detallado de los trabajos OCR del caso: progreso por documento,
    carpeta, confianza y opción de cancelar."""
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        # Jobs activos o recientes de tipo file_ingest / document_ocr.
        jobs = rows(c, """
            SELECT j.id, j.job_type, j.status, j.attempts, j.error_code,
                   j.input_ids, j.created_at, j.updated_at,
                   j.pipeline_version, j.model_version
            FROM jobs j
            WHERE j.case_id = :c
              AND j.job_type IN ('file_ingest', 'document_ocr', 'media_asr')
              AND j.status IN ('QUEUED', 'RUNNING', 'RETRYING', 'SUCCEEDED', 'FAILED')
            ORDER BY j.created_at DESC
            LIMIT 50
        """, c=str(case_id))

        result = []
        for job in jobs:
            input_ids = [str(i) for i in (job["input_ids"] or [])]
            if not input_ids:
                result.append({**job, "items": []})
                continue

            # Documentos del job con su estado y carpeta.
            docs = rows(c, """
                SELECT d.id, d.filename, d.processing_status, d.page_count, d.folder_id,
                       pr.pct AS live_pct,
                       (SELECT count(*) FROM document_pages p WHERE p.document_id = d.id AND p.human_corrected) AS human_corrected_pages,
                       (SELECT count(*) FROM document_pages p WHERE p.document_id = d.id) AS pages_done,
                       (SELECT round(avg(p.ocr_confidence), 3) FROM document_pages p WHERE p.document_id = d.id) AS confidence_avg
                FROM documents d
                LEFT JOIN document_ocr_progress pr ON pr.document_id = d.id
                WHERE d.id = ANY(:ids)
            """, ids=input_ids)

            # Medias del job (ASR).
            medias = rows(c, """
                SELECT m.id, m.filename, m.processing_status, m.duration_ms,
                       (SELECT count(*) FROM transcript_segments s WHERE s.media_id = m.id) AS segments_done
                FROM media m
                WHERE m.id = ANY(:ids)
            """, ids=input_ids)

            items = []
            for d in docs:
                folder_path = _folder_path(c, d["folder_id"]) if d["folder_id"] else "Raíz del proceso"
                progress = 0
                status = d["processing_status"]
                if d["processing_status"] in ("OCR_COMPLETE", "REVIEW_REQUIRED"):
                    progress = 100
                elif d["live_pct"] is not None:
                    # Progress en vivo: la tx del pipeline no ha commiteado el estado.
                    progress = min(99, int(d["live_pct"]))
                    status = "OCR_RUNNING"
                elif d["processing_status"] in ("UPLOADED", "OCR_PENDING", "OCR_RUNNING"):
                    total = d["page_count"] or 0
                    done = d["pages_done"] or 0
                    progress = min(99, int(100 * done / total)) if total > 0 else 2
                elif d["processing_status"] == "FAILED":
                    progress = 0
                items.append({
                    "id": str(d["id"]),
                    "kind": "document",
                    "filename": d["filename"],
                    "folder_path": folder_path,
                    "status": status,
                    "progress": progress,
                    "page_count": d["page_count"],
                    "pages_done": d["pages_done"],
                    "human_corrected_pages": d["human_corrected_pages"],
                    "confidence_avg": float(d["confidence_avg"]) if d["confidence_avg"] is not None else None,
                })

            for m in medias:
                folder_path = _folder_path(c, m["folder_id"]) if m["folder_id"] else "Raíz del proceso"
                progress = 0
                if m["processing_status"] in ("ASR_COMPLETE", "REVIEW_REQUIRED"):
                    progress = 100
                elif m["processing_status"] == "ASR_RUNNING":
                    progress = 50  # ASR no tiene páginas; estimación simple.
                items.append({
                    "id": str(m["id"]),
                    "kind": "media",
                    "filename": m["filename"],
                    "folder_path": folder_path,
                    "status": m["processing_status"],
                    "progress": progress,
                    "duration_ms": m["duration_ms"],
                    "segments_done": m["segments_done"],
                })

            # Progreso global del job (media de los items).
            global_progress = round(sum(i["progress"] for i in items) / len(items)) if items else 0

            result.append({
                "id": str(job["id"]),
                "job_type": job["job_type"],
                "status": job["status"],
                "attempts": job["attempts"],
                "error_code": job["error_code"],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "progress": global_progress,
                "items": items,
                "can_cancel": job["status"] in ("QUEUED", "RUNNING", "RETRYING"),
            })

        return {"case_id": str(case_id), "jobs": result}


def _folder_path(conn, folder_id: str | None) -> str:
    """Devuelve la ruta completa de una carpeta (ej. '01PrimeraInstancia/0001 DemandaPrincipal')."""
    if not folder_id:
        return "Raíz del proceso"
    parts = []
    cur = folder_id
    while cur:
        row = one(conn, "SELECT id, parent_id, name FROM case_folders WHERE id = :f", f=cur)
        if row is None:
            break
        parts.insert(0, row["name"])
        cur = str(row["parent_id"]) if row["parent_id"] else None
    return "/".join(parts) if parts else "Raíz del proceso"


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel_job(job_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    """Cancela un job QUEUED/RUNNING/RETRYING. Los documentos ya procesados se conservan."""
    with tx(p.org_id, p.user_id) as c:
        job = one(c, "SELECT id, status, case_id, job_type FROM jobs WHERE id = :i", i=str(job_id))
        if not job:
            raise AppError("NOT_FOUND", 404)
        # Verificar acceso al caso del job.
        case_access(p, UUID(str(job["case_id"])), "job.run")
        if job["status"] not in ("QUEUED", "RUNNING", "RETRYING"):
            raise AppError("INVALID_STATE", 409, f"Job en estado {job['status']} no se puede cancelar")
        one(c, "UPDATE jobs SET status = 'CANCELLED', updated_at = now() WHERE id = :i RETURNING id", i=str(job_id))
        # Marcar los documentos del job como pendientes para posible reproceso.
        if job["job_type"] in ("file_ingest", "document_ocr"):
            c.execute(text("""
                UPDATE documents SET processing_status = 'OCR_PENDING'
                WHERE id = ANY(SELECT unnest(input_ids) FROM jobs WHERE id = :i)
                  AND processing_status IN ('OCR_RUNNING', 'OCR_PENDING')
            """), {"i": str(job_id)})
        elif job["job_type"] == "media_asr":
            c.execute(text("""
                UPDATE media SET processing_status = 'ASR_PENDING'
                WHERE id = ANY(SELECT unnest(input_ids) FROM jobs WHERE id = :i)
                  AND processing_status IN ('ASR_RUNNING', 'ASR_PENDING')
            """), {"i": str(job_id)})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="job.cancelled",
                     entity_type="job", entity_id=str(job_id),
                     after={"job_type": job["job_type"], "status": "CANCELLED"}, request=request)
    return {"job_id": str(job_id), "status": "CANCELLED"}


@router.get("/{case_id}/export")
def export_case(case_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    """Exporta el expediente como CKP (Case Knowledge Package) en formato ZIP."""
    case_access(p, case_id, "ai.export")
    with tx(p.org_id, p.user_id) as c:
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="case.exported", entity_type="case",
                     entity_id=str(case_id), request=request)
        data = export.export_case(c, case_id)
    from fastapi.responses import Response
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{export.export_filename(case_id)}"'},
    )


@router.post("/{case_id}/ckp/persist")
def persist_ckp(case_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    """Persiste el CKP como archivos sueltos en el object storage (además del ZIP).

    Crea un snapshot inmutable en `cases/{id}/ckp/{timestamp}/…` con la misma
    estructura que el ZIP (document.md, chunks.jsonl, events/timeline.json, …) y
    actualiza el puntero `cases/{id}/ckp/latest.json`.
    """
    case_access(p, case_id, "ai.export")
    with tx(p.org_id, p.user_id) as c:
        result = export.persist_case_package(c, case_id)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="case.ckp_persisted",
                     entity_type="case", entity_id=str(case_id),
                     after={"snapshot": result["snapshot"], "files": result["files"], "bytes": result["bytes"]},
                     request=request)
    return result


def _list(case_id: UUID, p: Principal, sql: str) -> list[dict]:
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        return rows(c, sql, c=str(case_id))


@router.get("/{case_id}/claims")
def claims(case_id: UUID, p: Principal = Depends(current_principal)):
    return _list(case_id, p, """SELECT cl.id, cl.text, cl.claim_type, cl.claimant_party_id, pa.name AS claimant, cl.confidence,
        cl.review_status, cl.version,
        (SELECT coalesce(json_agg(ci.id), '[]') FROM citations ci WHERE ci.target_type='claim' AND ci.target_id=cl.id) AS citation_ids
        FROM claims cl LEFT JOIN parties pa ON pa.id = cl.claimant_party_id WHERE cl.case_id = :c ORDER BY cl.created_at""")


@router.get("/{case_id}/facts")
def facts(case_id: UUID, p: Principal = Depends(current_principal)):
    return _list(case_id, p, """SELECT f.id, f.proposition, f.status, f.determined_by_decision_id, f.review_status, f.version,
        (SELECT coalesce(json_agg(json_build_object('claim_id', fc.claim_id, 'stance', fc.stance)), '[]') FROM fact_claims fc WHERE fc.fact_id=f.id) AS claims,
        (SELECT coalesce(json_agg(json_build_object('evidence_id', el.evidence_id, 'stance', el.stance)), '[]') FROM evidence_links el WHERE el.fact_id=f.id) AS evidence
        FROM facts f WHERE f.case_id = :c""")


@router.get("/{case_id}/evidence")
def evidence(case_id: UUID, p: Principal = Depends(current_principal)):
    """Matriz de evidencia (SSD §108): el estado se deriva de vínculos, no de una puntuación."""
    return _list(case_id, p, """SELECT f.id AS fact_id, f.proposition, f.status AS fact_status, e.id AS evidence_id, e.evidence_type,
        e.description, e.source_document_id, e.source_media_id, el.stance
        FROM evidence_links el JOIN evidence e ON e.id = el.evidence_id JOIN facts f ON f.id = el.fact_id
        WHERE f.case_id = :c ORDER BY f.proposition""")


@router.get("/{case_id}/contradictions")
def contradictions(case_id: UUID, p: Principal = Depends(current_principal)):
    return _list(case_id, p, """SELECT co.id, co.contradiction_type, co.description, co.severity, co.human_review_required,
        co.review_status, co.version, co.claim_a_id, a.text AS claim_a_text, co.claim_b_id, b.text AS claim_b_text
        FROM contradictions co JOIN claims a ON a.id=co.claim_a_id JOIN claims b ON b.id=co.claim_b_id WHERE co.case_id=:c""")


@router.get("/{case_id}/timeline")
def timeline(case_id: UUID, kind: str | None = None, instance: str | None = None,
             actor: str | None = None, p: Principal = Depends(current_principal)):
    """Línea de tiempo: eventos PROCESALES (actuaciones) y genéricos.
    Filtros opcionales: kind=procedural|generic, instance, actor."""
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        evs = [dict(r) for r in rows(c, """
            SELECT e.id, e.kind, e.event_date, e.date_precision, e.event_type, e.subtype, e.instance,
                   e.actor, e.authority, e.date_type, e.procedural_effect, e.description,
                   e.timeline_confidence, e.confidence, e.document_id, e.page_number,
                   e.duplicate_of, e.review_flags,
                   d.filename AS document_filename,
                   (SELECT coalesce(json_agg(json_build_object('citation_id', ci.id, 'source_type', ci.source_type,
                        'document_id', ci.document_id, 'page', ci.page_number, 'media_id', ci.media_id,
                        'start_ms', ci.start_ms, 'end_ms', ci.end_ms,
                        'filename', coalesce(dc.filename, m.filename))), '[]')
                     FROM citations ci
                     LEFT JOIN documents dc ON dc.id = ci.document_id
                     LEFT JOIN media m ON m.id = ci.media_id
                     WHERE ci.target_type = 'event' AND ci.target_id = e.id) AS sources
            FROM events e
            LEFT JOIN documents d ON d.id = e.document_id
            WHERE e.case_id = :c
              AND (CAST(:kind AS text) IS NULL OR e.kind = :kind)
              AND (CAST(:instance AS text) IS NULL OR e.instance = :instance)
              AND (CAST(:actor AS text) IS NULL OR e.actor = :actor)
            ORDER BY e.event_date NULLS LAST, e.kind""",
            c=str(case_id), kind=kind, instance=instance, actor=actor)]
        rels = rows(c, """SELECT source_event_id, target_event_id, relationship, confidence
                          FROM event_relationships WHERE case_id = :c""", c=str(case_id))
        code_map = ct_read.event_code_map(c, str(case_id))
    for e in evs:
        code = code_map.get(str(e["id"]))
        e["code"] = code
        e["seq"] = int(code.split("-")[1]) if code else None
    idx = {str(e["id"]): e for e in evs}
    links_map: dict[str, list[dict[str, object]]] = {k: [] for k in idx}
    for r in rels:
        s, t = str(r["source_event_id"]), str(r["target_event_id"])
        if s in idx and t in idx:
            links_map[s].append({"relationship": r["relationship"], "direction": "out",
                                 "other_id": t, "other_code": idx[t]["code"],
                                 "other_subtype": idx[t]["subtype"], "confidence": r["confidence"]})
            links_map[t].append({"relationship": r["relationship"], "direction": "in",
                                 "other_id": s, "other_code": idx[s]["code"],
                                 "other_subtype": idx[s]["subtype"], "confidence": r["confidence"]})
    for e in evs:
        e["links"] = links_map.get(str(e["id"]), [])
    return evs


@router.post("/{case_id}/timeline/build")
def build_timeline(case_id: UUID, request: Request, llm: bool = False,
                   p: Principal = Depends(current_principal)):
    """Recalcula el Process Graph: relaciones entre actuaciones, resolución de eventos referenciados
    y marcas de revisión. Con `llm=true`, un segundo pase con IA propone relaciones causales."""
    case = case_access(p, case_id, "media.upload")
    if case["status"] == "ARCHIVED":
        raise AppError("INVALID_STATE_TRANSITION", 409)
    with tx(p.org_id, p.user_id) as c:
        linked = procedural_graph.link_events(c, p.org_id, str(case_id))
        reviewed = procedural_graph.review_events(c, p.org_id, str(case_id))
        ai = procedural_graph.propose_relations_llm(c, p.org_id, str(case_id), p.user_id) if llm else None
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="timeline.built", entity_type="case",
                     entity_id=str(case_id), after={"linked": linked, "reviewed": reviewed, "ai": ai},
                     request=request)
    return {"linked": linked, "reviewed": reviewed, "ai_links": ai}


@router.get("/{case_id}/entities")
def entities(case_id: UUID, p: Principal = Depends(current_principal)):
    return _list(case_id, p, "SELECT id, entity_type, name, normalized_name, aliases, resolution_status, party_id FROM entities WHERE case_id=:c")


@router.get("/{case_id}/parties")
def parties(case_id: UUID, p: Principal = Depends(current_principal)):
    return _list(case_id, p, "SELECT id, name, role, entity_type, aliases FROM parties WHERE case_id=:c ORDER BY name")


@router.post("/{case_id}/parties/extract")
def extract_parties(case_id: UUID, p: Principal = Depends(current_principal)):
    """Candidatos de partes (demandante/demandado/…) detectados en los encabezados de los autos.
    NO persiste: devuélvelos al panel de revisión y confirma las correctas con POST /parties."""
    case_access(p, case_id, "media.upload")
    with tx(p.org_id, p.user_id) as c:
        return party_extraction.extract(c, str(case_id))


@router.post("/{case_id}/parties", status_code=201)
def create_parties(case_id: UUID, body: PartyBulkIn, request: Request, p: Principal = Depends(current_principal)):
    """Confirma/crea una o varias partes (deduplica por nombre normalizado)."""
    case = case_access(p, case_id, "media.upload")
    if case["status"] == "ARCHIVED":
        raise AppError("INVALID_STATE_TRANSITION", 409)
    with tx(p.org_id, p.user_id) as c:
        created = party_extraction.create_parties(
            c, p.org_id, str(case_id), p.user_id, [b.model_dump() for b in body.parties])
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="parties.created", entity_type="case",
                     entity_id=str(case_id),
                     after={"count": len(created), "names": [x["name"] for x in created]}, request=request)
    return {"created": created}


@router.get("/{case_id}/speakers")
def speakers(case_id: UUID, p: Principal = Depends(current_principal)):
    return _list(case_id, p, "SELECT id, label, display_name, speaker_role, resolved_party_id, resolution_status, resolution_source, confidence, version FROM speakers WHERE case_id=:c ORDER BY label")


@router.get("/{case_id}/speakers/role-suggestions")
def speaker_role_suggestions(case_id: UUID, p: Principal = Depends(current_principal)):
    """Sugiere un rol a cada hablante a partir de los candidatos detectados en las firmas de los documentos."""
    case_access(p, case_id, "media.read")
    with tx(p.org_id, p.user_id) as c:
        return ct_read.role_suggestions(c, str(case_id))


@router.post("/{case_id}/speakers/roles")
def assign_speaker_roles(case_id: UUID, body: SpeakerRoleAssign, request: Request,
                         p: Principal = Depends(current_principal)):
    """Asigna rol (y parte opcional) a varios hablantes de una vez (deja EXACTO el conteo por rol)."""
    case = case_access(p, case_id, "media.upload")
    if case["status"] == "ARCHIVED":
        raise AppError("INVALID_STATE_TRANSITION", 409)
    updated: list[str] = []
    with tx(p.org_id, p.user_id) as c:
        for a in body.assignments:
            if not one(c, "SELECT id FROM speakers WHERE id = :i AND case_id = :c",
                       i=str(a.speaker_id), c=str(case_id)):
                continue
            party = _valid_party(c, case_id, a.resolved_party_id)
            one(c, """UPDATE speakers SET speaker_role = :r, resolved_party_id = :pid, version = version + 1
                      WHERE id = :i RETURNING id""",
                r=(a.speaker_role or "").strip() or None, pid=party, i=str(a.speaker_id))
            updated.append(str(a.speaker_id))
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="speaker.roles_assigned", entity_type="case",
                     entity_id=str(case_id), after={"count": len(updated)}, request=request)
    return {"updated": updated}


@router.post("/{case_id}/speakers", status_code=201)
def create_speaker(case_id: UUID, body: SpeakerCreate, request: Request, p: Principal = Depends(current_principal)):
    """Crea un hablante manual con su nombre visible, su rol (juez/apoderado/…) y parte opcional."""
    case = case_access(p, case_id, "media.upload")
    if case["status"] == "ARCHIVED":
        raise AppError("INVALID_STATE_TRANSITION", 409)
    with tx(p.org_id, p.user_id) as c:
        party = _valid_party(c, case_id, body.resolved_party_id)
        row = one(c, """SELECT COALESCE(MAX(NULLIF(regexp_replace(label, '\\D', '', 'g'), '')::int), 0) AS n
                        FROM speakers WHERE case_id = :c AND label ~ '^SPEAKER_[0-9]+$'""", c=str(case_id))
        label = f"SPEAKER_{int(row['n']) + 1:02d}"
        spk = one(c, """INSERT INTO speakers (organization_id, case_id, label, display_name, speaker_role,
                              resolved_party_id, resolution_status, resolution_source, confidence)
            VALUES (:o, :c, :l, :n, :r, :pid, 'PROBABLE', 'manual', 1.0)
            RETURNING id, label, display_name, speaker_role, resolved_party_id, resolution_status, resolution_source, confidence, version""",
                  o=p.org_id, c=str(case_id), l=label, n=body.display_name.strip(),
                  r=(body.speaker_role or "").strip() or None, pid=party)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="speaker.created", entity_type="speaker",
                     entity_id=str(spk["id"]),
                     after={"display_name": body.display_name.strip(), "speaker_role": spk["speaker_role"],
                            "resolved_party_id": party, "via": "manual"}, request=request)
    return spk


@router.delete("/{case_id}/speakers/{speaker_id}")
def delete_speaker(case_id: UUID, speaker_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    """Elimina un hablante: sus segmentos quedan SIN hablante (la cita se conserva) y se reindexa/grafo."""
    case = case_access(p, case_id, "media.upload")
    if case["status"] == "ARCHIVED":
        raise AppError("INVALID_STATE_TRANSITION", 409)
    with tx(p.org_id, p.user_id) as c:
        spk = one(c, "SELECT id, label, display_name FROM speakers WHERE id = :i AND case_id = :c",
                  i=str(speaker_id), c=str(case_id))
        if not spk:
            raise AppError("NOT_FOUND", 404)
        media_ids = [str(r["media_id"]) for r in rows(
            c, "SELECT DISTINCT media_id FROM transcript_segments WHERE speaker_id = :s", s=str(speaker_id))]
        moved = one(c, "SELECT count(*) AS n FROM transcript_segments WHERE speaker_id = :s",
                    s=str(speaker_id))["n"]
        c.execute(text("UPDATE transcript_segments SET speaker_id = :u WHERE speaker_id = :s"),
                  {"u": _ensure_unidentified(c, p.org_id, str(case_id)), "s": str(speaker_id)})
        c.execute(text("DELETE FROM speakers WHERE id = :i"), {"i": str(speaker_id)})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="speaker.deleted", entity_type="speaker",
                     entity_id=str(speaker_id),
                     after={"label": spk["label"], "display_name": spk["display_name"],
                            "segments_unassigned": moved}, request=request)
    for mid in media_ids:
        try:
            with tx(p.org_id, p.user_id) as c:
                indexing.index_media(c, p.org_id, str(case_id), mid, actor_id=p.user_id)
        except Exception:  # noqa: BLE001
            log.exception("no se pudo reindexar el media %s tras borrar el hablante", mid)
    try:
        enqueue_graph_refresh(p.org_id, str(case_id), p.user_id)
    except Exception:  # noqa: BLE001
        log.exception("no se pudo encolar el grafo tras borrar el hablante (caso %s)", case_id)
    return {"deleted": str(speaker_id), "segments_unassigned": moved, "media_affected": len(media_ids)}


def _valid_party(c, case_id: UUID, party_id) -> str | None:
    """Valida que la parte pertenezca al caso y devuelve su id (o None)."""
    if party_id is None:
        return None
    if not one(c, "SELECT id FROM parties WHERE id = :i AND case_id = :c", i=str(party_id), c=str(case_id)):
        raise AppError("NOT_FOUND", 404)
    return str(party_id)


def _ensure_unidentified(c, org_id: str, case_id: str) -> str:
    """Id del hablante 'Sin identificar' del caso (lo crea si no existe)."""
    row = one(c, """SELECT id FROM speakers WHERE case_id = :c
                    AND (label = 'UNKNOWN' OR lower(coalesce(display_name, '')) = 'sin identificar')
                    ORDER BY label LIMIT 1""", c=case_id)
    if row:
        one(c, """UPDATE speakers SET display_name = 'Sin identificar'
                  WHERE id = :i AND coalesce(display_name, '') = '' RETURNING id""", i=str(row["id"]))
        return str(row["id"])
    new = one(c, """INSERT INTO speakers (organization_id, case_id, label, display_name,
                          resolution_status, resolution_source, confidence)
        VALUES (:o, :c, 'UNKNOWN', 'Sin identificar', 'UNRESOLVED', 'system', 0.0) RETURNING id""",
              o=org_id, c=case_id)
    return str(new["id"])


@router.patch("/{case_id}/speakers/{speaker_id}")
def update_speaker(case_id: UUID, speaker_id: UUID, body: SpeakerPatch, request: Request,
                   p: Principal = Depends(current_principal)):
    """Edita el nombre, el rol y/o la parte asociada de un hablante (para que los conteos por rol
    sean EXACTOS en vez de heurísticos)."""
    case = case_access(p, case_id, "media.upload")
    if case["status"] == "ARCHIVED":
        raise AppError("INVALID_STATE_TRANSITION", 409)
    with tx(p.org_id, p.user_id) as c:
        if not one(c, "SELECT id FROM speakers WHERE id = :i AND case_id = :c", i=str(speaker_id), c=str(case_id)):
            raise AppError("NOT_FOUND", 404)
        sets, params, after = ["version = version + 1"], {}, {}
        if "display_name" in body.model_fields_set:
            sets.append("display_name = :n")
            params["n"] = (body.display_name or "").strip() or None
            after["display_name"] = params["n"]
        if "speaker_role" in body.model_fields_set:
            sets.append("speaker_role = :r")
            params["r"] = (body.speaker_role or "").strip() or None
            after["speaker_role"] = params["r"]
        if "resolved_party_id" in body.model_fields_set:
            sets.append("resolved_party_id = :pid")
            params["pid"] = _valid_party(c, case_id, body.resolved_party_id)
            after["resolved_party_id"] = params["pid"]
        m = one(c, f"""UPDATE speakers SET {', '.join(sets)} WHERE id = :i
            RETURNING id, label, display_name, speaker_role, resolved_party_id, resolution_status,
                      resolution_source, confidence, version""", i=str(speaker_id), **params)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="speaker.updated", entity_type="speaker",
                     entity_id=str(speaker_id), after=after, request=request)
    return m


@router.post("/{case_id}/speakers/merge")
def merge_speakers(case_id: UUID, body: SpeakerMergeIn, request: Request, p: Principal = Depends(current_principal)):
    """Fusiona dos hablantes que son la misma persona: reasigna segmentos, borra el duplicado,
    reindexa pgvector y encola la reconstrucción del grafo."""
    case = case_access(p, case_id, "media.upload")
    if case["status"] == "ARCHIVED":
        raise AppError("INVALID_STATE_TRANSITION", 409)
    keep, merge = str(body.keep_speaker_id), str(body.merge_speaker_id)
    if keep == merge:
        raise AppError("VALIDATION_ERROR", 422, [{"field": "merge_speaker_id", "type": "same_as_keep"}])
    with tx(p.org_id, p.user_id) as c:
        found = {str(r["id"]) for r in rows(
            c, "SELECT id FROM speakers WHERE case_id = :c AND id IN (:k, :m)", c=str(case_id), k=keep, m=merge)}
        if keep not in found or merge not in found:
            raise AppError("NOT_FOUND", 404)
        media_ids = speakers_service.merge(c, str(case_id), keep, merge)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="speaker.merged", entity_type="speaker",
                     entity_id=keep, after={"merged_id": merge, "via": "api"}, request=request)
    for mid in media_ids:  # propagación fuera de la transacción
        try:
            with tx(p.org_id, p.user_id) as c:
                indexing.index_media(c, p.org_id, str(case_id), mid, actor_id=p.user_id)
        except Exception:  # noqa: BLE001
            log.exception("no se pudo reindexar el media %s tras fusionar hablantes", mid)
    try:
        enqueue_graph_refresh(p.org_id, str(case_id), p.user_id)
    except Exception:  # noqa: BLE001
        log.exception("no se pudo encolar el grafo tras fusionar hablantes (caso %s)", case_id)
    return {"keep_speaker_id": keep, "merge_speaker_id": merge, "media_affected": len(media_ids)}
