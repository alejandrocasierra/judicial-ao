from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import one, tx
from app.core.errors import AppError
from app.schemas import LoginIn, RefreshIn
from app.security.deps import Principal, current_principal
from app.security.passwords import verify_password
from app.security.tokens import access_token, decode, hash_refresh, refresh_token
from app.services import audit, ratelimit

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(c, user_id: str, org_id: str, role: str) -> dict:
    s = get_settings()
    rt, rt_hash = refresh_token(user_id, org_id)
    c.execute(text("INSERT INTO refresh_tokens (organization_id, user_id, token_hash, expires_at) VALUES (:o,:u,:h,:e)"),
              {"o": org_id, "u": user_id, "h": rt_hash,
               "e": datetime.now(timezone.utc) + timedelta(seconds=s.REFRESH_TOKEN_TTL_SECONDS)})
    return {"access_token": access_token(user_id, org_id, role), "refresh_token": rt, "token_type": "bearer",
            "expires_in": s.ACCESS_TOKEN_TTL_SECONDS}


@router.post("/login")
def login(body: LoginIn, request: Request):
    s = get_settings()
    ratelimit.check("login", f"{request.client.host if request.client else 'na'}:{body.email.lower()}")
    with tx(None) as c:
        u = one(c, "SELECT * FROM auth_find_user(:e)", e=body.email)
    ok = verify_password(body.password, u["password_hash"] if u else None)  # tiempo constante aunque no exista
    if not u:
        with tx(None) as c:
            audit.record(c, org_id=None, actor_id=None, action="auth.login_failed", request=request)
        raise AppError("AUTH_INVALID_CREDENTIALS", 401)
    org, uid = str(u["organization_id"]), str(u["id"])
    if u["locked_until"] and u["locked_until"] > datetime.now(timezone.utc):
        with tx(org) as c:
            audit.record(c, org_id=org, actor_id=uid, action="auth.login_locked", request=request)
        raise AppError("AUTH_ACCOUNT_LOCKED", 423)
    if not ok or not u["is_active"]:
        with tx(org) as c:
            if not ok:
                c.execute(text("SELECT auth_register_failure(:u, :m, :l)"),
                          {"u": uid, "m": s.AUTH_MAX_FAILED_ATTEMPTS, "l": s.AUTH_LOCKOUT_SECONDS})
            audit.record(c, org_id=org, actor_id=uid, action="auth.login_failed", request=request)
        raise AppError("AUTH_INVALID_CREDENTIALS", 401)  # inactivo => mismo mensaje (anti-enumeración)
    with tx(org, uid) as c:
        c.execute(text("SELECT auth_register_success(:u)"), {"u": uid})
        tokens = _issue(c, uid, org, u["org_role"])
        audit.record(c, org_id=org, actor_id=uid, action="auth.login", request=request)
    return tokens


@router.post("/refresh")
def refresh(body: RefreshIn, request: Request):
    data = decode(body.refresh_token, "refresh")
    org, uid = data["org"], data["sub"]
    reuse = False
    with tx(org, uid) as c:
        row = one(c, "SELECT id, revoked_at, expires_at FROM refresh_tokens WHERE token_hash = :h AND user_id = :u FOR UPDATE",
                  h=hash_refresh(data.get("rt", "")), u=uid)
        if not row or row["revoked_at"] is not None:
            # reutilización de refresh token revocado => revocar toda la familia (posible robo).
            # La revocación debe CONFIRMARSE: se sale de la transacción antes de lanzar el error.
            c.execute(text("UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"), {"u": uid})
            audit.record(c, org_id=org, actor_id=uid, action="auth.refresh_reuse_detected", request=request)
            reuse = True
    if reuse:
        raise AppError("AUTH_TOKEN_INVALID", 401)
    with tx(org, uid) as c:
        row = one(c, "SELECT id, revoked_at, expires_at FROM refresh_tokens WHERE token_hash = :h AND user_id = :u FOR UPDATE",
                  h=hash_refresh(data.get("rt", "")), u=uid)
        if not row or row["revoked_at"] is not None:
            raise AppError("AUTH_TOKEN_INVALID", 401)
        u = one(c, "SELECT org_role, is_active FROM users WHERE id = :u", u=uid)
        if not u or not u["is_active"]:
            raise AppError("AUTH_TOKEN_INVALID", 401)
        c.execute(text("UPDATE refresh_tokens SET revoked_at = now() WHERE id = :i"), {"i": row["id"]})
        return _issue(c, uid, org, u["org_role"])


@router.get("/me")
def me(p: Principal = Depends(current_principal)):
    with tx(p.org_id, p.user_id) as c:
        u = one(c, "SELECT id, email, full_name, org_role, locale, organization_id FROM users WHERE id = :u", u=p.user_id)
    return {**u, "permissions": sorted(p.permissions)}
