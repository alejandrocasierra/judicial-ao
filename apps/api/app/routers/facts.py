"""API del ledger de hechos por parte."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.db import tx
from app.core.errors import AppError
from app.schemas import FactReviewIn, FactUpdateIn
from app.security.deps import Principal, case_access, current_principal
from app.services import audit, party_facts
from app.workers.dispatcher import create_job, enqueue_if_pending

router = APIRouter(prefix="/cases/{case_id}", tags=["facts"])


@router.get("/facts")
def list_facts(case_id: UUID, proof: str = "all", side: str | None = None,
               party_id: UUID | None = None, issue_id: UUID | None = None,
               q: str | None = None, limit: int = 200, envelope: bool = False,
               p: Principal = Depends(current_principal)):
    """Lista hechos. El contrato histórico es una lista; `envelope=true` permite
    a la UI pedir `{facts: [...]}` sin romper clientes anteriores."""
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as conn:
        facts = party_facts.list_facts(
            conn, str(case_id), proof=proof, side=side,
            party_id=str(party_id) if party_id else None,
            issue_id=str(issue_id) if issue_id else None, q=q, limit=limit)
        return {"facts": facts} if envelope else facts


@router.get("/facts/export")
def export_facts(case_id: UUID, format: str = "json", p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as conn:
        facts = party_facts.list_facts(conn, str(case_id), proof="all", limit=500)
    if format == "csv":
        lines = ["id,proposition,proof_status,is_proven,side,party,position"]
        for f in facts:
            for pos in f.get("positions") or []:
                lines.append(",".join(_csv(x) for x in (
                    f.get("id"), f.get("proposition"), f.get("proof_status"),
                    f.get("is_proven"), pos.get("side"), pos.get("party_name"),
                    pos.get("position"))))
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("\n".join(lines), media_type="text/csv")
    return {"facts": facts}


@router.post("/facts/rebuild")
def rebuild_facts(case_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "facts.write")
    from app.core.config import get_settings
    s = get_settings()
    with tx(p.org_id, p.user_id) as conn:
        job = create_job(conn, org_id=str(p.org_id), case_id=str(case_id),
                         job_type="facts_build", input_ids=[str(case_id)],
                         actor_id=str(p.user_id), key_parts=["facts_build", str(case_id)],
                         pipeline_version=s.PIPELINE_VERSION, model_version="heuristic")
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="facts.rebuild_requested",
                     entity_type="case", entity_id=str(case_id), after={"job_id": str(job["id"])},
                     request=request)
    enqueue_if_pending(job, str(p.org_id), str(p.user_id))
    return {"job_id": str(job["id"]), "status": job["status"], "reused": job.get("reused", False)}


@router.post("/facts/extract-positions")
def extract_positions(case_id: UUID, request: Request, dry_run: bool = True,
                      limit: int = 5000, p: Principal = Depends(current_principal)):
    """Extrae alega/niega/admite desde claims con cita.

    `dry_run=true` (por defecto) solo reporta los cambios. No convierte UNKNOWN
    en ADMITS: exige verbo explícito y hablante identificado.
    """
    case_access(p, case_id, "facts.write")
    with tx(p.org_id, p.user_id) as conn:
        result = party_facts.extract_positions_from_claims(
            conn, str(p.org_id), str(case_id), str(p.user_id),
            dry_run=dry_run, limit=limit)
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id,
                     action="facts.extract_positions", entity_type="case", entity_id=str(case_id),
                     after={"dry_run": dry_run, "inserted": result["inserted"],
                            "updated": result["updated"], "unknown": result["unknown"]},
                     request=request)
    return result


@router.get("/facts/{fact_id}")
def get_fact(case_id: UUID, fact_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as conn:
        fact = party_facts.get_fact(conn, str(case_id), str(fact_id), with_revisions=True)
        if not fact:
            raise AppError("FACT_NOT_FOUND", 404)
        return fact


@router.get("/facts/{fact_id}/revisions")
def fact_revisions(case_id: UUID, fact_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as conn:
        fact = party_facts.get_fact(conn, str(case_id), str(fact_id), with_revisions=True)
        if not fact:
            raise AppError("FACT_NOT_FOUND", 404)
        return {"revisions": fact.get("revisions") or []}


@router.patch("/facts/{fact_id}")
def update_fact(case_id: UUID, fact_id: UUID, body: FactUpdateIn, request: Request,
                p: Principal = Depends(current_principal)):
    case_access(p, case_id, "facts.write")
    with tx(p.org_id, p.user_id) as conn:
        try:
            result = party_facts.update_fact(
                conn, org_id=str(p.org_id), case_id=str(case_id), fact_id=str(fact_id),
                expected_version=body.expected_version, actor_id=str(p.user_id),
                reason=body.reason, proof_status=body.proof_status,
                positions=[x.model_dump() for x in body.positions] if body.positions is not None else None,
                proposition=body.proposition, issue_id=str(body.issue_id) if body.issue_id else None,
                fact_code=body.fact_code)
        except LookupError as exc:
            raise AppError("FACT_NOT_FOUND", 404) from exc
        except party_facts.ProofStatusDisabled as exc:
            raise AppError("FACTS_PROOF_DISABLED", 409, {
                "detail": "La transición a un estado probatorio está deshabilitada. "
                          "Activa FACTS_PROOF_ENABLED tras migrar y backfillear."}) from exc
        except ValueError as exc:
            raise AppError("FACT_VALIDATION_ERROR", 422, {"detail": str(exc)}) from exc
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="fact.updated",
                     entity_type="fact", entity_id=str(fact_id),
                     after={"proof_status": result.get("proof_status"), "reason": body.reason},
                     request=request)
        return result


@router.post("/facts/{fact_id}/review")
def review_fact(case_id: UUID, fact_id: UUID, body: FactReviewIn, request: Request,
                p: Principal = Depends(current_principal)):
    case_access(p, case_id, "facts.review")
    with tx(p.org_id, p.user_id) as conn:
        try:
            result = party_facts.review_fact(
                conn, org_id=str(p.org_id), case_id=str(case_id), fact_id=str(fact_id),
                actor_id=str(p.user_id), action=body.action, reason=body.reason,
                changes=body.changes)
        except LookupError as exc:
            raise AppError("FACT_NOT_FOUND", 404) from exc
        except ValueError as exc:
            raise AppError("FACT_REVIEW_ERROR", 422, {"detail": str(exc)}) from exc
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="fact.reviewed",
                     entity_type="fact", entity_id=str(fact_id),
                     after={"action": body.action, "reason": body.reason}, request=request)
        return result


def _csv(value) -> str:
    text = str(value if value is not None else "")
    if any(c in text for c in ',"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text
