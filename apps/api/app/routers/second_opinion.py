"""API de segunda opinion por proceso/demanda."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import one, tx
from app.core.errors import AppError
from app.schemas import SecondOpinionReviewIn
from app.security.deps import Principal, case_access, current_principal
from app.services import audit, second_opinion
from app.workers.dispatcher import create_job, enqueue_if_pending

router = APIRouter(prefix="/cases/{case_id}", tags=["second-opinion"])


@router.post("/messages/{message_id}/second-opinion")
def create_second_opinion(case_id: UUID, message_id: UUID, request: Request,
                          p: Principal = Depends(current_principal)):
    case_access(p, case_id, "second_opinion.run")
    if not get_settings().SECOND_OPINION_ENABLED:
        raise AppError("SECOND_OPINION_DISABLED", 409)
    s = get_settings()
    with tx(p.org_id, p.user_id) as conn:
        try:
            run = second_opinion.create_run(
                conn, org_id=str(p.org_id), case_id=str(case_id),
                message_id=str(message_id), actor_id=str(p.user_id))
        except LookupError as exc:
            raise AppError("MESSAGE_NOT_FOUND", 404) from exc
        except ValueError as exc:
            code = str(exc)
            raise AppError(code.upper(), 422, {"detail": code}) from exc
        if run.get("status") in {"RUNNING", "COMPLETED"}:
            # Ya esta corriendo o termino: no se duplica el job.
            job = None
        else:
            # QUEUED y FAILED se pueden (re)encolar; la clave de jobs es idempotente.
            job = create_job(conn, org_id=str(p.org_id), case_id=str(case_id),
                             job_type="second_opinion", input_ids=[str(run["id"])],
                             actor_id=str(p.user_id), key_parts=["second_opinion", str(run["id"])],
                             pipeline_version=s.PIPELINE_VERSION,
                             model_version=str(run.get("verifier_model_name") or "verifier"))
            if job.get("reused") and job.get("status") == "FAILED":
                conn.execute(text("UPDATE jobs SET status='QUEUED', error_code=NULL WHERE id=:j"),
                             {"j": str(job["id"])})
                job["status"] = "QUEUED"
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="second_opinion.requested",
                     entity_type="second_opinion_run", entity_id=str(run["id"]),
                     after={"message_id": str(message_id)}, request=request)
    if job:
        enqueue_if_pending(job, str(p.org_id), str(p.user_id))
    return {"run": run, "job_id": str(job["id"]) if job else None}


@router.get("/second-opinions")
def list_second_opinions(case_id: UUID, status: str | None = None, limit: int = 50,
                         p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as conn:
        return {"runs": second_opinion.list_runs(conn, str(p.org_id), str(case_id), limit=limit)}


@router.get("/second-opinions/{run_id}")
def get_second_opinion(case_id: UUID, run_id: UUID,
                       p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as conn:
        run = second_opinion.get_run(conn, str(p.org_id), str(case_id), str(run_id))
        if not run:
            raise AppError("SECOND_OPINION_NOT_FOUND", 404)
        return run


@router.post("/second-opinions/{run_id}/cancel")
def cancel_second_opinion(case_id: UUID, run_id: UUID, request: Request,
                          p: Principal = Depends(current_principal)):
    case_access(p, case_id, "second_opinion.run")
    with tx(p.org_id, p.user_id) as conn:
        row = one(conn, "SELECT * FROM second_opinion_runs WHERE id=:r AND case_id=:c",
                  r=str(run_id), c=str(case_id))
        if not row:
            raise AppError("SECOND_OPINION_NOT_FOUND", 404)
        if row["status"] not in {"QUEUED", "RUNNING"}:
            return {"run_id": str(run_id), "status": row["status"], "cancelled": False}
        conn.execute(text("UPDATE second_opinion_runs SET status='CANCELLED', completed_at=now() WHERE id=:r"),
                     {"r": str(run_id)})
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="second_opinion.cancelled",
                     entity_type="second_opinion_run", entity_id=str(run_id), request=request)
        return {"run_id": str(run_id), "status": "CANCELLED", "cancelled": True}


@router.post("/second-opinions/{run_id}/review")
def review_second_opinion(case_id: UUID, run_id: UUID, body: SecondOpinionReviewIn,
                          request: Request, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "second_opinion.run")
    with tx(p.org_id, p.user_id) as conn:
        row = one(conn, "SELECT * FROM second_opinion_runs WHERE id=:r AND case_id=:c",
                  r=str(run_id), c=str(case_id))
        if not row:
            raise AppError("SECOND_OPINION_NOT_FOUND", 404)
        conn.execute(text("""
            INSERT INTO reviews (organization_id, case_id, entity_type, entity_id, action,
                                 reviewer_id, reason, original_output, human_output)
            VALUES (:o,:c,'second_opinion',:r,:a,:u,:why,CAST(:orig AS jsonb),CAST(:human AS jsonb))
        """), {"o": str(p.org_id), "c": str(case_id), "r": str(run_id), "a": body.action,
               "u": str(p.user_id), "why": body.reason,
               "orig": json.dumps(row, ensure_ascii=False, default=str),
               "human": json.dumps(body.changes or {}, ensure_ascii=False, default=str)})
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="second_opinion.reviewed",
                     entity_type="second_opinion_run", entity_id=str(run_id),
                     after={"action": body.action}, request=request)
        return {"ok": True, "run_id": str(run_id), "action": body.action}
