"""SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación).
Los tokens maliciosos se fabrican con la configuración del entorno de pruebas (nada quemado)."""
from __future__ import annotations

import base64
import json
import time
import uuid

import jwt
import pytest

from helpers import assert_error


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


@pytest.fixture
def forge(settings, client, auth):
    me = client.get("/v1/auth/me", headers=auth("abogada.alfa")).json()

    def _f(secret: str | None = None, alg: str | None = None, **overrides) -> str:
        now = int(time.time())
        claims = {"sub": me["id"], "org": me["organization_id"], "role": me["org_role"], "iss": settings.JWT_ISSUER,
                  "aud": settings.JWT_AUDIENCE, "iat": now, "nbf": now, "exp": now + 300, "jti": str(uuid.uuid4()),
                  "typ": "access"}
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, secret if secret is not None else settings.JWT_SECRET, algorithm=alg or settings.JWT_ALGORITHM)
    _f.me = me
    return _f


def _me(client, token: str):
    return client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})


def test_sec_jwt_01_valid_forged_with_real_secret_works(client, forge):
    """Control positivo: el fabricador produce tokens válidos, así los negativos prueban lo que dicen."""
    assert _me(client, forge()).status_code == 200


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "bearer\tabc", "Token abc"])
def test_sec_jwt_02_missing_or_malformed_header(client, header):
    r = client.get("/v1/auth/me", headers={"Authorization": header} if header else {})
    assert r.status_code == 401 and r.json()["error"]["code"] in ("AUTH_REQUIRED", "AUTH_TOKEN_INVALID")


def test_sec_jwt_03_alg_none_is_rejected(client, forge):
    good = jwt.decode(forge(), options={"verify_signature": False})
    token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(good)}."
    assert_error(_me(client, token), 401, "AUTH_TOKEN_INVALID")


def test_sec_jwt_04_wrong_secret_is_rejected(client, forge):
    assert_error(_me(client, forge(secret=uuid.uuid4().hex * 2)), 401, "AUTH_TOKEN_INVALID")


def test_sec_jwt_05_algorithm_confusion_is_rejected(client, forge, settings):
    other = "HS512" if settings.JWT_ALGORITHM != "HS512" else "HS384"
    assert_error(_me(client, forge(alg=other)), 401, "AUTH_TOKEN_INVALID")


def test_sec_jwt_06_expired_token(client, forge):
    past = int(time.time()) - 3600
    assert_error(_me(client, forge(iat=past - 60, nbf=past - 60, exp=past)), 401, "AUTH_TOKEN_EXPIRED")


@pytest.mark.parametrize("claim,value", [("aud", "otra-api"), ("iss", "otro-emisor"), ("typ", "refresh"),
                                         ("typ", None), ("exp", None), ("sub", None)])
def test_sec_jwt_07_wrong_or_missing_claims(client, forge, claim, value):
    assert _me(client, forge(**{claim: value})).status_code == 401


def test_sec_jwt_08_org_claim_forgery_cannot_cross_tenant(client, forge, org_ids):
    """Aun con la firma correcta, cambiar 'org' no da acceso: el usuario no existe bajo el RLS de otro tenant."""
    assert_error(_me(client, forge(org=org_ids["beta"])), 401, "AUTH_TOKEN_INVALID")


def test_sec_jwt_09_role_claim_is_not_trusted(client, forge, ids):
    """El rol del token se ignora: la autorización usa el rol vigente en la base de datos."""
    t = forge(role="ORG_ADMIN")
    assert _me(client, t).json()["org_role"] == forge.me["org_role"]
    assert_error(client.get("/v1/audit", headers={"Authorization": f"Bearer {t}"}), 403, "FORBIDDEN")


def test_sec_jwt_10_refresh_token_cannot_be_used_as_access(client, email, password):
    r = client.post("/v1/auth/login", json={"email": email("lector.alfa"), "password": password}).json()
    assert_error(_me(client, r["refresh_token"]), 401, "AUTH_TOKEN_INVALID")
    assert_error(client.post("/v1/auth/refresh", json={"refresh_token": r["access_token"]}), 401, "AUTH_TOKEN_INVALID")


def test_sec_jwt_11_tampered_payload_invalidates_signature(client, forge):
    h, p, s = forge().split(".")
    claims = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    claims["role"] = "ORG_ADMIN"
    assert_error(_me(client, f"{h}.{_b64(claims)}.{s}"), 401, "AUTH_TOKEN_INVALID")


def test_sec_jwt_12_token_never_contains_sensitive_data(client, email, password):
    r = client.post("/v1/auth/login", json={"email": email("lector.alfa"), "password": password}).json()
    for tok in (r["access_token"], r["refresh_token"]):
        claims = jwt.decode(tok, options={"verify_signature": False})
        blob = json.dumps(claims)
        assert password not in blob and "password" not in blob and "@" not in blob
