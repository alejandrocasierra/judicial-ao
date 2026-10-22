"""API de auditoria legal y politica de verificacion por proceso."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.schemas import AuditReviewIn, MessageReleaseIn, VerificationPolicyIn
from app.security.deps import Principal, case_access, current_principal
from app.services import audit, legal_guard, legal_release

router = APIRouter(prefix="/cases/{case_id}", tags=["audits"])

# Lista blanca interna de columnas actualizables de la política. El nombre de la
# columna nunca viene del cliente: se deriva de este mapa y el valor va por bind.
_POLICY_COLUMNS = {
    "mode": "mode",
    "require_primary_citation": "require_primary_citation",
    "require_legal_norm": "require_legal_norm",
    "block_on_unsupported": "block_on_unsupported",
    "block_on_numbers": "block_on_numbers",
    "block_on_parties": "block_on_parties",
    "block_on_legal_conclusion": "block_on_legal_conclusion",
    "require_second_opinion": "require_second_opinion",
    "max_unsupported_claims": "max_unsupported_claims",
    "min_ocr_confidence": "min_ocr_confidence",
    "min_asr_confidence": "min_asr_confidence",
}


def _audit_row(conn, audit_id: str):
    return one(conn, "SELECT * FROM answer_audits WHERE id=:a", a=audit_id)


@router.get("/audits")
def list_audits(case_id: UUID, status: str | None = None, message_id: UUID | None = None,
                limit: int = 50, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    where = ["organization_id=:o", "case_id=:c"]
    params: dict = {"o": str(p.org_id), "c": str(case_id), "l": max(1, min(int(limit), 200))}
    if status:
        where.append("status=:s")
        params["s"] = status.upper()
    if message_id:
        where.append("chat_message_id=:m")
        params["m"] = str(message_id)
    with tx(p.org_id, p.user_id) as conn:
        sql = """
            SELECT id, chat_message_id, model_run_id, status, mode, claims_total,
                   claims_supported, claims_blocked, findings_critical, duration_ms, created_at
            FROM answer_audits
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT :l"
        return {"audits": rows(conn, sql, **params)}


@router.post("/messages/{message_id}/audit")
def reaudit_message(case_id: UUID, message_id: UUID, request: Request,
                    p: Principal = Depends(current_principal)):
    case_access(p, case_id, "audit.run")
    with tx(p.org_id, p.user_id) as conn:
        result = legal_guard.audit_message(
            conn, org_id=str(p.org_id), case_id=str(case_id),
            message_id=str(message_id), actor_id=str(p.user_id))
        if result is None:
            raise AppError("AUDIT_CONTEXT_MISSING", 404, {
                "detail": "El mensaje no tiene model_run/claims auditables."})
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="answer.audited",
                     entity_type="chat_message", entity_id=str(message_id),
                     after={"audit_id": result.get("audit_id"), "status": result.get("status")},
                     request=request)
        return result


@router.get("/audits/{audit_id}")
def get_audit(case_id: UUID, audit_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as conn:
        row = one(conn, "SELECT * FROM answer_audits WHERE id=:a AND case_id=:c",
                  a=str(audit_id), c=str(case_id))
        if not row:
            raise AppError("AUDIT_NOT_FOUND", 404)
        row["claims"] = rows(conn, "SELECT * FROM answer_audit_claims WHERE audit_id=:a ORDER BY claim_index, auditor",
                             a=str(audit_id))
        row["citations"] = rows(conn, "SELECT * FROM answer_audit_citations WHERE audit_id=:a ORDER BY id",
                                a=str(audit_id))
        row["findings"] = rows(conn, "SELECT * FROM answer_audit_findings WHERE audit_id=:a ORDER BY id",
                               a=str(audit_id))
        return row


@router.post("/audits/{audit_id}/review")
def review_audit(case_id: UUID, audit_id: UUID, body: AuditReviewIn, request: Request,
                 p: Principal = Depends(current_principal)):
    case_access(p, case_id, "audit.review")
    with tx(p.org_id, p.user_id) as conn:
        row = _audit_row(conn, str(audit_id))
        if not row or str(row["case_id"]) != str(case_id):
            raise AppError("AUDIT_NOT_FOUND", 404)
        current = row
        current["claims"] = rows(conn, "SELECT * FROM answer_audit_claims WHERE audit_id=:a ORDER BY claim_index",
                                 a=str(audit_id))
        current["findings"] = rows(conn, "SELECT * FROM answer_audit_findings WHERE audit_id=:a ORDER BY id",
                                   a=str(audit_id))
        conn.execute(text("""
            INSERT INTO reviews (organization_id, case_id, entity_type, entity_id, action,
                                 reviewer_id, reason, original_output, human_output)
            VALUES (:o,:c,'answer_audit',:a,:act,:u,:r,CAST(:orig AS jsonb),CAST(:human AS jsonb))
        """), {"o": str(p.org_id), "c": str(case_id), "a": str(audit_id), "act": body.action,
               "u": str(p.user_id), "r": body.reason,
               "orig": json.dumps(current, ensure_ascii=False, default=str),
               "human": json.dumps(body.changes or {}, ensure_ascii=False, default=str)})
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="answer.reviewed",
                     entity_type="answer_audit", entity_id=str(audit_id),
                     after={"action": body.action, "reason": body.reason}, request=request)
        return {"ok": True, "audit_id": str(audit_id), "action": body.action}


@router.post("/messages/{message_id}/release")
def release_message(case_id: UUID, message_id: UUID, body: MessageReleaseIn,
                    request: Request, p: Principal = Depends(current_principal)):
    """Libera una respuesta retenida después de una segunda opinión completada.

    No es automático: la decisión humana queda en `audit_logs` y la respuesta
    original se copia a `chat_messages`. Si el veredicto es DISAGREE, requiere
    `force=true`.
    """
    case_access(p, case_id, "audit.review")
    with tx(p.org_id, p.user_id) as conn:
        try:
            result = legal_release.release_held_message(
                conn, org_id=str(p.org_id), case_id=str(case_id),
                message_id=str(message_id), reason=body.reason, force=body.force)
        except legal_release.ReleaseError as exc:
            raise AppError(exc.code, 409, {"detail": exc.detail}) from exc
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id,
                     action="answer.released", entity_type="chat_message",
                     entity_id=str(message_id),
                     after={"run_id": str(result["run"]["id"]),
                            "verdict": result["run"].get("verdict"),
                            "reason": body.reason}, request=request)
        return {"released": True, "message": result["message"],
                "run_id": str(result["run"]["id"]),
                "verdict": result["run"].get("verdict")}


@router.get("/verification-policy")
def get_policy(case_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as conn:
        policy = legal_guard.load_policy(conn, str(p.org_id), str(case_id))
        settings = one(conn, "SELECT * FROM case_verification_settings WHERE case_id=:c", c=str(case_id))
        return {"policy": policy, "settings": settings}


@router.put("/verification-policy")
def put_policy(case_id: UUID, body: VerificationPolicyIn, request: Request,
               p: Principal = Depends(current_principal)):
    case_access(p, case_id, "verification_policy.write")
    with tx(p.org_id, p.user_id) as conn:
        policy = legal_guard.load_policy(conn, str(p.org_id), str(case_id))
        policy_id = policy.get("id")
        if policy_id is None or policy.get("case_id") is None:
            # Crear una politica propia del caso; no se modifica la global por accidente.
            row = one(conn, """
                INSERT INTO legal_guard_policies
                  (organization_id, case_id, name, mode, require_primary_citation, require_legal_norm,
                   block_on_unsupported, block_on_numbers, block_on_parties, block_on_legal_conclusion,
                   require_second_opinion, second_opinion_model_id, max_unsupported_claims,
                   min_ocr_confidence, min_asr_confidence, version, created_by)
                VALUES (:o,:c,:n,'BALANCED',true,true,true,true,true,true,false,NULL,0,0.55,0.55,1,:u)
                RETURNING id
            """, o=str(p.org_id), c=str(case_id), n="Politica del proceso", u=str(p.user_id))
            policy_id = str(row["id"])
        fields = body.model_dump(exclude_unset=True)
        for key, column in _POLICY_COLUMNS.items():
            if key in fields and fields[key] is not None:
                conn.execute(text("UPDATE legal_guard_policies SET " + column +
                                  "=:v, version=version+1, updated_at=now() WHERE id=:p"),
                             {"v": fields[key], "p": policy_id})
        if "second_opinion_model_id" in fields:
            model_id = fields["second_opinion_model_id"]
            if model_id:
                m = one(conn, "SELECT id FROM ai_models WHERE id=:i AND organization_id=:o",
                        i=str(model_id), o=str(p.org_id))
                if not m:
                    raise AppError("MODEL_NOT_FOUND", 404)
            conn.execute(text("UPDATE legal_guard_policies SET second_opinion_model_id=:m, updated_at=now() WHERE id=:p"),
                         {"m": str(model_id) if model_id else None, "p": policy_id})
        enabled = fields.get("second_opinion_enabled")
        auto = fields.get("auto_second_opinion")
        model_setting = fields.get("second_opinion_model_id", None)
        conn.execute(text("""
            INSERT INTO case_verification_settings
              (case_id, organization_id, policy_id, second_opinion_enabled,
               second_opinion_model_id, auto_second_opinion, updated_by, updated_at)
            VALUES (:c,:o,:p,coalesce(:en,false),:m,coalesce(:auto,'NEVER'),:u,now())
            ON CONFLICT (case_id) DO UPDATE SET
              policy_id=EXCLUDED.policy_id,
              second_opinion_enabled=coalesce(:en, case_verification_settings.second_opinion_enabled),
              second_opinion_model_id=CASE WHEN :m IS NULL THEN case_verification_settings.second_opinion_model_id ELSE :m END,
              auto_second_opinion=coalesce(:auto, case_verification_settings.auto_second_opinion),
              updated_by=:u, updated_at=now()
        """), {"c": str(case_id), "o": str(p.org_id), "p": policy_id, "en": enabled,
               "m": str(model_setting) if model_setting else None, "auto": auto, "u": str(p.user_id)})
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="verification.policy_updated",
                     entity_type="case", entity_id=str(case_id),
                     after={"policy_id": policy_id, "fields": fields}, request=request)
        return {"policy": legal_guard.load_policy(conn, str(p.org_id), str(case_id))}
