from __future__ import annotations

import hashlib
import secrets
import time
import uuid

import jwt

from app.core.config import get_settings
from app.core.errors import AppError


def _encode(claims: dict, ttl: int, typ: str) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {**claims, "iss": s.JWT_ISSUER, "aud": s.JWT_AUDIENCE, "iat": now, "nbf": now,
               "exp": now + ttl, "jti": str(uuid.uuid4()), "typ": typ}
    return jwt.encode(payload, s.JWT_SECRET, algorithm=s.JWT_ALGORITHM)


def access_token(user_id: str, org_id: str, org_role: str) -> str:
    return _encode({"sub": user_id, "org": org_id, "role": org_role}, get_settings().ACCESS_TOKEN_TTL_SECONDS, "access")


def refresh_token(user_id: str, org_id: str) -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    tok = _encode({"sub": user_id, "org": org_id, "rt": raw}, get_settings().REFRESH_TOKEN_TTL_SECONDS, "refresh")
    return tok, hashlib.sha256(raw.encode()).hexdigest()


def decode(token: str, expected_typ: str) -> dict:
    s = get_settings()
    try:
        # algorithms fijado desde config: bloquea "alg=none" y confusión de algoritmos
        data = jwt.decode(token, s.JWT_SECRET, algorithms=[s.JWT_ALGORITHM], audience=s.JWT_AUDIENCE,
                          issuer=s.JWT_ISSUER, options={"require": ["exp", "iat", "sub", "iss", "aud", "typ"]})
    except jwt.ExpiredSignatureError:
        raise AppError("AUTH_TOKEN_EXPIRED", 401) from None
    except jwt.PyJWTError:
        raise AppError("AUTH_TOKEN_INVALID", 401) from None
    if data.get("typ") != expected_typ:
        raise AppError("AUTH_TOKEN_INVALID", 401)
    return data


def hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
