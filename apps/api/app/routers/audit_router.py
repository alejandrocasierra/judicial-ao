from fastapi import APIRouter, Depends, Query

from app.core.db import rows, tx
from app.security.deps import Principal, require_org

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_audit(limit: int = Query(default=100, ge=1, le=1000), action: str | None = Query(default=None, max_length=100),
               p: Principal = Depends(require_org("audit.read"))):
    with tx(p.org_id, p.user_id) as c:
        return rows(c, """SELECT id, actor_id, action, entity_type, entity_id, ip, request_id, created_at, hash
            FROM audit_logs WHERE (CAST(:a AS text) IS NULL OR action = :a) ORDER BY id DESC LIMIT :l""", a=action, l=limit)
