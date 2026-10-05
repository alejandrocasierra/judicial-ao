"""SEC-ERR: manejo de errores y cabeceras (OWASP A05). Nunca stack traces ni detalles internos."""
from __future__ import annotations

import uuid

import pytest

from helpers import assert_error


def test_sec_err_01_unhandled_exception_is_generic_500(raw_client, auth, ids, monkeypatch):
    from app.services import answering

    def boom(*a, **k):
        raise RuntimeError("detalle interno secreto: postgres://usuario:clave@host")
    monkeypatch.setattr(answering, "retrieve", boom)
    r = raw_client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"), json={"question": "pago", "mode": "fact_lookup"})
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secreto" not in r.text and "RuntimeError" not in r.text and "Traceback" not in r.text


def test_sec_err_02_security_headers(client, auth):
    r = client.get("/v1/auth/me", headers=auth("lector.alfa"))
    for h, v in {"x-content-type-options": "nosniff", "x-frame-options": "DENY", "referrer-policy": "no-referrer",
                 "cache-control": "no-store"}.items():
        assert r.headers.get(h) == v
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_sec_err_03_request_id_echo_and_sanitization(client):
    good = f"req-{uuid.uuid4().hex[:12]}"
    assert client.get("/health", headers={"X-Request-ID": good}).headers["x-request-id"] == good
    for bad in ("<script>alert(1)</script>", "a" * 200, "id;rm -rf /"):
        rid = client.get("/health", headers={"X-Request-ID": bad}).headers["x-request-id"]
        assert rid != bad and len(rid) == 36


@pytest.mark.parametrize("method,path,status,code", [
    ("get", "/v1/no-existe", 404, "NOT_FOUND"), ("put", "/v1/cases", 405, "METHOD_NOT_ALLOWED"),
])
def test_sec_err_04_unknown_routes_use_error_contract(client, method, path, status, code):
    en = assert_error(getattr(client, method)(path, headers={"Accept-Language": "en"}), status, code)
    es = assert_error(getattr(client, method)(path, headers={"Accept-Language": "es"}), status, code)
    assert en["message"] != es["message"]


def test_sec_err_05_no_server_banner_leak(client):
    r = client.get("/health")
    assert "x-powered-by" not in r.headers
