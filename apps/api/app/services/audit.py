"""Audit log (SSD §25.5, §86). Nunca registra contenido jurídico completo."""
from __future__ import annotations

import json

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.engine import Connection


def _ip(request: Request | None) -> str | None:
    return request.client.host if request and request.client else None


def record(conn: Connection, *, org_id: str | None, actor_id: str | None, action: str,
           entity_type: str | None = None, entity_id: str | None = None,
           before: dict | None = None, after: dict | None = None, request: Request | None = None) -> None:
    conn.execute(text(
        "INSERT INTO audit_logs (organization_id, actor_id, action, entity_type, entity_id, before, after, ip, request_id) "
        "VALUES (:o, :a, :act, :et, :eid, CAST(:b AS jsonb), CAST(:af AS jsonb), :ip, :rid)"),
        {"o": org_id, "a": actor_id, "act": action, "et": entity_type, "eid": entity_id,
         "b": json.dumps(before, default=str) if before is not None else None,
         "af": json.dumps(after, default=str) if after is not None else None,
         "ip": _ip(request), "rid": getattr(request.state, "request_id", None) if request else None})
