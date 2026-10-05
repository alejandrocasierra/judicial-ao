"""Modelo de error de la API (SSD §118). Nunca expone stack traces."""
from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.i18n import negotiate, t


class AppError(Exception):
    def __init__(self, code: str, status: int, details: dict | list | None = None):
        self.code, self.status, self.details = code, status, details


def _body(request: Request, code: str, details=None) -> dict:
    loc = getattr(request.state, "locale", None) or negotiate(request.headers.get("accept-language"))
    err = {"code": code, "message": t(f"errors.{code}", loc), "request_id": getattr(request.state, "request_id", None)}
    if details is not None:
        err["details"] = details
    return {"error": err}


async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(_body(request, exc.code, exc.details), status_code=exc.status)


async def validation_error_handler(request: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "type": e["type"]} for e in exc.errors()]
    return JSONResponse(_body(request, "VALIDATION_ERROR", details), status_code=422)


async def unhandled_error_handler(request: Request, exc: Exception):
    import logging
    logging.getLogger("app").exception("unhandled_error", extra={"request_id": getattr(request.state, "request_id", None)})
    return JSONResponse(_body(request, "INTERNAL_ERROR"), status_code=500)


_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "AUTH_REQUIRED", 403: "FORBIDDEN"}


async def http_error_handler(request: Request, exc):
    """Rutas inexistentes / métodos no permitidos con el mismo contrato de error e i18n."""
    code = _HTTP_CODES.get(exc.status_code, "VALIDATION_ERROR" if exc.status_code < 500 else "INTERNAL_ERROR")
    return JSONResponse(_body(request, code), status_code=exc.status_code)
