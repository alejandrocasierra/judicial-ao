from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from fastapi import Depends, Request

from app.core.db import one, tx
from app.core.errors import AppError
from app.security import rbac
from app.security.tokens import decode
from app.services import roles as roles_service


@dataclass
class Principal:
    user_id: str
    org_id: str
    org_role: str
    locale: str
    permissions: set[str] = field(default_factory=set)


def _load_principal(request: Request, token: str) -> Principal:
    data = decode(token, "access")
    with tx(data["org"]) as c:
        u = one(c, "SELECT id, org_role, locale, is_active FROM users WHERE id = :id", id=data["sub"])
        if u and u["is_active"]:
            roles_service.load_into_rbac(c, data["org"])
    if not u or not u["is_active"]:
        raise AppError("AUTH_TOKEN_INVALID", 401)
    p = Principal(str(u["id"]), data["org"], u["org_role"], u["locale"],
                  rbac.org_permissions(u["org_role"], data["org"]))
    request.state.principal = p
    return p


def current_principal(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise AppError("AUTH_REQUIRED", 401)
    return _load_principal(request, auth[7:].strip())


def download_principal(request: Request) -> Principal:
    """Como current_principal, pero también acepta ?token= en la URL.

    Necesario para <video> e <img>, que no pueden enviar cabeceras; así el
    navegador hace streaming (HTTP Range) sin descargar el archivo completo."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return _load_principal(request, auth[7:].strip())
    token = request.query_params.get("token") or ""
    if not token:
        raise AppError("AUTH_REQUIRED", 401)
    return _load_principal(request, token)


def require_org(permission: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if permission not in p.permissions:
            raise AppError("FORBIDDEN", 403)
        return p
    return dep


def case_access(p: Principal, case_id: UUID | str, permission: str) -> dict:
    """Carga el expediente (bajo RLS) y verifica el permiso. 404 si no existe o es de otro tenant
    (no se revela existencia); 403 si existe pero sin permiso."""
    with tx(p.org_id, p.user_id) as c:
        case = one(c, "SELECT * FROM cases WHERE id = :id", id=str(case_id))
        if not case:
            raise AppError("CASE_NOT_FOUND", 404)
        m = one(c, "SELECT case_role FROM case_members WHERE case_id = :c AND user_id = :u", c=str(case_id), u=p.user_id)
    perms = rbac.case_permissions(p.org_role, m["case_role"] if m else None, p.org_id)
    if "case.read" not in perms:
        raise AppError("CASE_NOT_FOUND", 404)
    if permission not in perms:
        raise AppError("FORBIDDEN", 403)
    return case
