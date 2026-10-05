"""Revisión humana (SSD §34, §95, §125-126): transaccional, con bloqueo optimista y
conservando la salida original de IA."""
from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.domain.states import FACT_STATUSES
from app.schemas import ReviewIn
from app.security.deps import Principal, case_access, current_principal
from app.services import audit, indexing
from app.workers.handlers.file_ingest import enqueue_graph_refresh

router = APIRouter(prefix="/review", tags=["review"])
log = logging.getLogger(__name__)

TABLES = {"claim": "claims", "fact": "facts", "speaker": "speakers", "contradiction": "contradictions"}
EDITABLE = {
    "claim": {"text", "claim_type", "claimant_party_id", "temporal_scope"},
    "fact": {"proposition", "status", "determined_by_decision_id"},
    "speaker": {"display_name", "resolved_party_id", "resolution_status", "speaker_role"},
    "contradiction": {"description", "severity"},
}
ACTION_TO_STATUS = {"ACCEPT": "ACCEPTED", "EDIT": "EDITED", "REJECT": "REJECTED", "FLAG": "FLAGGED"}


@router.post("/{entity_id}")
def review(entity_id: UUID, body: ReviewIn, request: Request, p: Principal = Depends(current_principal)):
    table = TABLES[body.entity_type]
    with tx(p.org_id, p.user_id) as c:
        current = one(c, f"SELECT * FROM {table} WHERE id = :i", i=str(entity_id))
    if not current:
        raise AppError("ENTITY_NOT_FOUND", 404)
    case_access(p, current["case_id"], "review.write")

    changes = dict(body.changes)
    bad = set(changes) - EDITABLE[body.entity_type]
    if bad:
        raise AppError("REVIEW_FIELD_NOT_ALLOWED", 422, {"fields": sorted(bad)})
    if body.action != "EDIT" and changes:
        raise AppError("REVIEW_ACTION_INVALID", 422)
    if body.action == "EDIT" and not changes:
        raise AppError("REVIEW_ACTION_INVALID", 422)
    if body.entity_type == "fact" and "status" in changes:
        if changes["status"] not in FACT_STATUSES:
            raise AppError("VALIDATION_ERROR", 422, {"field": "status"})
        decision = changes.get("determined_by_decision_id", current.get("determined_by_decision_id"))
        if changes["status"] == "JUDICIALLY_DETERMINED" and not decision:
            raise AppError("JUDICIAL_DETERMINATION_REQUIRES_DECISION", 422)
    if body.entity_type == "speaker" and changes.get("resolution_status") == "CONFIRMED":
        if not changes.get("resolved_party_id", current.get("resolved_party_id")):
            raise AppError("SPEAKER_CONFIRMATION_REQUIRES_PARTY", 422)

    if body.entity_type != "speaker":
        changes["review_status"] = ACTION_TO_STATUS[body.action]
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    try:
        with tx(p.org_id, p.user_id) as c:
            upd = one(c, f"UPDATE {table} SET {sets}{', ' if sets else ''}version = version + 1 "
                         "WHERE id = :i AND version = :v RETURNING *", i=str(entity_id), v=body.expected_version, **changes)
            if not upd:
                raise AppError("VERSION_CONFLICT", 409)
            original = current.get("original_ai_output") or {k: current[k] for k in EDITABLE[body.entity_type] if k in current}
            human = {k: upd[k] for k in EDITABLE[body.entity_type] if k in upd}
            c.execute(text("INSERT INTO reviews (organization_id, case_id, entity_type, entity_id, action, reviewer_id, reason, original_output, human_output) "
                           "VALUES (:o,:c,:et,:e,:a,:r,:why,CAST(:orig AS jsonb),CAST(:hum AS jsonb))"),
                      {"o": p.org_id, "c": str(current["case_id"]), "et": body.entity_type, "e": str(entity_id), "a": body.action,
                       "r": p.user_id, "why": body.reason, "orig": json.dumps(original, default=str), "hum": json.dumps(human, default=str)})
            audit.record(c, org_id=p.org_id, actor_id=p.user_id, action=f"{body.entity_type}.{body.action.lower()}",
                         entity_type=body.entity_type, entity_id=str(entity_id),
                         before={k: current.get(k) for k in changes}, after=changes, request=request)
    except IntegrityError as e:
        msg = str(e.orig)
        if "JUDICIAL_DETERMINATION_REQUIRES_DECISION" in msg or "facts_check" in msg:
            raise AppError("JUDICIAL_DETERMINATION_REQUIRES_DECISION", 422) from None
        if "speakers_check" in msg:
            raise AppError("SPEAKER_CONFIRMATION_REQUIRES_PARTY", 422) from None
        raise AppError("VALIDATION_ERROR", 422) from None
    # Renombrar un hablante cambia su nombre en TODA la transcripción: además de la
    # BD (ya actualizada) hay que refrescar pgvector (metadatos del chunk) y el grafo.
    if body.entity_type == "speaker" and "display_name" in changes:
        _propagate_speaker_rename(p, str(current["case_id"]), str(entity_id))
    return {"id": str(entity_id), "entity_type": body.entity_type, "action": body.action, "version": upd["version"]}


def _propagate_speaker_rename(p: Principal, case_id: str, speaker_id: str) -> None:
    """Reindexa en pgvector y encola el grafo de los media donde habla este hablante.

    Nunca rompe la respuesta: si falla, se registra y se sigue (la BD y el `review`
    ya quedaron consistentes)."""
    with tx(p.org_id, p.user_id) as c:
        media_ids = [r["media_id"] for r in rows(
            c, "SELECT DISTINCT media_id FROM transcript_segments WHERE speaker_id = :s", s=speaker_id)]
    for mid in media_ids:
        try:
            with tx(p.org_id, p.user_id) as c:
                indexing.index_media(c, p.org_id, case_id, str(mid), actor_id=p.user_id)
        except Exception:
            log.exception("no se pudo reindexar el media %s tras renombrar el hablante", mid)
    try:
        enqueue_graph_refresh(str(p.org_id), case_id, str(p.user_id), correction=True)
    except Exception:
        log.exception("no se pudo encolar graph_build tras renombrar el hablante (caso %s)", case_id)
