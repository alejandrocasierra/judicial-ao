"""Autenticación del servidor MCP: mismo JWT Bearer de la plataforma.

El middleware ASGI valida el token en cada request HTTP y fija el Principal
en un contextvar; las tools/recursos lo leen con current_principal() y abren
tx(org_id, actor_id) por llamada → RLS garantiza el aislamiento multi-tenant.
"""
from __future__ import annotations

import contextvars
import json
from typing import Any

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from app.core.db import one, tx
from app.core.errors import AppError
from app.security import rbac
from app.security.deps import Principal
from app.security.tokens import decode
from app.services import roles as roles_service

_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("mcp_principal", default=None)


def current_principal() -> Principal:
    p = _principal.get()
    if p is None:  # no debería ocurrir: el middleware valida antes
        raise McpError(ErrorData(code=401, message="AUTH_REQUIRED"))
    return p


def principal_from_header(auth: str) -> Principal:
    """Réplica de app.security.deps.current_principal sin Request de FastAPI."""
    if not auth.lower().startswith("bearer "):
        raise AppError("AUTH_REQUIRED", 401)
    data = decode(auth[7:].strip(), "access")
    with tx(data["org"]) as c:
        u = one(c, "SELECT id, org_role, locale, is_active FROM users WHERE id = :id", id=data["sub"])
        if u and u["is_active"]:
            roles_service.load_into_rbac(c, data["org"])
    if not u or not u["is_active"]:
        raise AppError("AUTH_TOKEN_INVALID", 401)
    return Principal(str(u["id"]), data["org"], u["org_role"], u["locale"],
                     rbac.org_permissions(u["org_role"], data["org"]))


class JwtAuthMiddleware:
    """ASGI puro (sin BaseHTTPMiddleware): Bearer JWT → Principal por request.

    Responde 401 con el contrato de error de la plataforma cuando el token
    falta o es inválido. Compatible con SSE/streaming (no envuelve el body).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode("latin-1")
        try:
            p = principal_from_header(auth)
        except AppError as e:
            body = json.dumps({"error": {"code": e.code, "message": e.code}}).encode()
            await send({"type": "http.response.start", "status": e.status,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        token = _principal.set(p)
        try:
            await self.app(scope, receive, send)
        finally:
            _principal.reset(token)
