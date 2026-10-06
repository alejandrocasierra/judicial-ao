"""IT-ASP — el administrador puede asignar contraseña al crear/editar usuarios.

Al crear/editar, un `password` opcional permite fijarla directamente (sin correo
de bienvenida). Debe cumplir la política; solo el ORG_ADMIN (permiso user.manage)
puede hacerlo.
"""
from __future__ import annotations

import uuid

import pytest
from helpers import assert_error

pytestmark = pytest.mark.integration

_STRONG = "Segura123!Abc"


def _cleanup(owner_db, email: str) -> None:
    with owner_db.cursor() as cur:
        cur.execute("DELETE FROM refresh_tokens WHERE user_id IN (SELECT id FROM users WHERE email = %s)", (email,))
        cur.execute("DELETE FROM users WHERE email = %s", (email,))
    owner_db.commit()


def _email(prefix: str) -> str:
    return f"{prefix}.{uuid.uuid4().hex[:8]}@example.test"


def test_it_asp_01_create_user_with_password_and_login(client, auth, owner_db):
    h = auth("admin.alfa")
    email = _email("pwset")
    try:
        r = client.post("/v1/admin/users", headers=h, json={
            "email": email, "full_name": "Usuario Prueba", "org_role": "ANALYST", "locale": "es", "password": _STRONG})
        assert r.status_code == 200, r.text
        assert r.json()["invite_sent"] is False
        login = client.post("/v1/auth/login", json={"email": email, "password": _STRONG})
        assert login.status_code == 200, login.text
    finally:
        _cleanup(owner_db, email)


def test_it_asp_02_create_user_weak_password_rejected(client, auth):
    h = auth("admin.alfa")
    r = client.post("/v1/admin/users", headers=h, json={
        "email": _email("weak"), "full_name": "Usuario Debil", "org_role": "ANALYST", "locale": "es",
        "password": "corta"})
    assert_error(r, 422, "PASSWORD_WEAK")


def test_it_asp_03_patch_password_changes_login(client, auth, owner_db):
    h = auth("admin.alfa")
    email = _email("pwpatch")
    new_pw = "OtraClave456!"
    try:
        created = client.post("/v1/admin/users", headers=h, json={
            "email": email, "full_name": "Usuario Dos", "org_role": "ANALYST", "locale": "es",
            "password": _STRONG}).json()
        uid = created["id"]
        u = client.get(f"/v1/admin/users/{uid}", headers=h).json()
        r = client.patch(f"/v1/admin/users/{uid}", headers=h,
                         json={"expected_version": u["version"], "password": new_pw})
        assert r.status_code == 200, r.text
        assert client.post("/v1/auth/login", json={"email": email, "password": new_pw}).status_code == 200
        assert client.post("/v1/auth/login", json={"email": email, "password": _STRONG}).status_code == 401
    finally:
        _cleanup(owner_db, email)


def test_it_asp_04_patch_weak_password_rejected(client, auth, owner_db):
    h = auth("admin.alfa")
    email = _email("pwpatchweak")
    try:
        created = client.post("/v1/admin/users", headers=h, json={
            "email": email, "full_name": "Usuario Tres", "org_role": "ANALYST", "locale": "es",
            "password": _STRONG}).json()
        uid = created["id"]
        u = client.get(f"/v1/admin/users/{uid}", headers=h).json()
        r = client.patch(f"/v1/admin/users/{uid}", headers=h,
                         json={"expected_version": u["version"], "password": "123"})
        assert_error(r, 422, "PASSWORD_WEAK")
    finally:
        _cleanup(owner_db, email)


def test_it_asp_05_non_admin_cannot_assign_password(client, auth):
    # LAWYER (abogada.alfa) no tiene user.manage
    r = client.post("/v1/admin/users", headers=auth("abogada.alfa"), json={
        "email": _email("noauth"), "full_name": "Sin Permiso", "org_role": "ANALYST", "locale": "es",
        "password": _STRONG})
    assert_error(r, 403, "FORBIDDEN")
