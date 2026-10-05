from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.db import one, tx
from app.core.errors import AppError
from app.security.deps import Principal, case_access, current_principal
from app.services import audit
from app.services.citations import validate

router = APIRouter(prefix="/citations", tags=["citations"])


@router.get("/{citation_id}")
def resolve(citation_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    with tx(p.org_id, p.user_id) as c:
        cit = one(c, "SELECT * FROM citations WHERE id = :i", i=str(citation_id))
    if not cit:
        raise AppError("CITATION_NOT_FOUND", 404)
    case_access(p, cit["case_id"], "document.read")
    with tx(p.org_id, p.user_id) as c:
        valid, reasons, source = validate(c, cit)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="citation.resolved", entity_type="citation",
                     entity_id=str(citation_id), request=request)
    return {"id": str(citation_id), "source_type": cit["source_type"], "target_type": cit["target_type"],
            "target_id": str(cit["target_id"]), "valid": valid, "invalid_reasons": reasons, "source": source}
