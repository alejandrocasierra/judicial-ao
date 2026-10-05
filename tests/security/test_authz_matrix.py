"""SEC-AUTHZ: matriz de autorización (SSD §19.1, §20.1, amenaza T2 — escalamiento de privilegios).
Permiso efectivo = rol de organización ∩ rol en el expediente (config/rbac.yaml).
Semillas, expediente "pago": gestor=OWNER, abogada=LAWYER, revisor=REVIEWER, analista(ANALYST)=REVIEWER,
lector=VIEWER; externo.alfa NO es miembro; admin.alfa es ORG_ADMIN (sin membresía)."""
from __future__ import annotations

import pytest

from helpers import assert_error

DENY_404 = ("externo.alfa", "admin.beta", "lawyer.beta")


def _call(client, auth, ids, user, action, pdf_bytes):
    h = auth(user)
    pago = ids["pago"]
    doc = ids["docs"]["01_demanda.pdf"]["id"]
    claim = ids["claims"][0]
    return {
        "read_case": lambda: client.get(f"/v1/cases/{pago}", headers=h),
        "list_claims": lambda: client.get(f"/v1/cases/{pago}/claims", headers=h),
        "view_page": lambda: client.get(f"/v1/cases/{pago}/documents/{doc}/pages/1", headers=h),
        "download": lambda: client.get(f"/v1/cases/{pago}/documents/{doc}/download", headers=h),
        "upload": lambda: client.post(f"/v1/cases/{pago}/documents", headers=h,
                                      files={"file": ("authz.pdf", pdf_bytes(), "application/pdf")}),
        "query": lambda: client.post(f"/v1/cases/{pago}/query", headers=h,
                                     json={"question": "¿Qué dice la demanda?", "mode": "document"}),
        "patch": lambda: client.patch(f"/v1/cases/{pago}", headers=h, json={"expected_version": 999999, "title": "Sin permiso"}),
        "process": lambda: client.post(f"/v1/cases/{pago}/process", headers=h, json={"job_types": ["indexing"]}),
        "review": lambda: client.post(f"/v1/review/{claim['id']}", headers=h, json={
            "entity_type": "claim", "action": "FLAG", "expected_version": 999999, "reason": "prueba de permisos"}),
        "members": lambda: client.post(f"/v1/cases/{pago}/members", headers=h,
                                       json={"user_id": claim["id"], "case_role": "VIEWER"}),
        "legal_hold": lambda: client.post(f"/v1/cases/{pago}/legal-hold", headers=h,
                                          json={"enabled": False, "reason": "prueba de permisos"}),
        "deletion_request": lambda: client.post(f"/v1/cases/{pago}/documents/{doc}/deletion-request", headers=h,
                                                json={"reason": "prueba de permisos de borrado"}),
        "audit": lambda: client.get("/v1/audit?limit=1", headers=h),
        "create_case": lambda: client.post("/v1/cases", headers=h, json={
            "jurisdiction": "co", "case_number": "1", "title": "Sin permiso", "language": "es"}),
    }[action]()


# (usuario, acción) que DEBEN ser rechazados con 403 (miembro sin permiso) — nunca 2xx
FORBIDDEN = [
    ("lector.alfa", a) for a in ("download", "upload", "query", "patch", "process", "review", "members", "create_case")
] + [
    ("analista.alfa", a) for a in ("download", "upload", "patch", "process", "review", "members", "create_case")
] + [
    ("revisor.alfa", a) for a in ("upload", "patch", "process", "members", "create_case")
] + [
    ("abogada.alfa", a) for a in ("members", "legal_hold", "deletion_request", "audit")
] + [
    ("gestor.alfa", a) for a in ("legal_hold", "deletion_request", "audit")
]


@pytest.mark.parametrize("user,action", FORBIDDEN)
def test_sec_authz_01_forbidden_actions(client, auth, ids, pdf_bytes, user, action):
    assert_error(_call(client, auth, ids, user, action, pdf_bytes), 403, "FORBIDDEN")


@pytest.mark.parametrize("user", DENY_404)
@pytest.mark.parametrize("action", ["read_case", "list_claims", "view_page", "download", "upload", "query", "patch", "process"])
def test_sec_authz_02_non_members_get_404_not_403(client, auth, ids, pdf_bytes, user, action):
    """No se revela la existencia del expediente a quien no es miembro (ni a otro tenant)."""
    assert_error(_call(client, auth, ids, user, action, pdf_bytes), 404, "CASE_NOT_FOUND")


ALLOWED = [
    ("lector.alfa", "read_case", 200), ("lector.alfa", "view_page", 200), ("lector.alfa", "list_claims", 200),
    ("analista.alfa", "query", 200), ("revisor.alfa", "download", 200), ("revisor.alfa", "query", 200),
    ("admin.alfa", "read_case", 200), ("admin.alfa", "audit", 200), ("admin.alfa", "download", 200),
]


@pytest.mark.parametrize("user,action,status", ALLOWED)
def test_sec_authz_03_allowed_actions(client, auth, ids, pdf_bytes, user, action, status):
    r = _call(client, auth, ids, user, action, pdf_bytes)
    assert r.status_code == status, r.text


@pytest.mark.parametrize("action", ["read_case", "query", "upload", "audit", "create_case"])
def test_sec_authz_04_anonymous_is_401(client, ids, pdf_bytes, action):
    anon = lambda local, lang=None: {}  # noqa: E731
    assert_error(_call(client, anon, ids, "anon", action, pdf_bytes), 401, "AUTH_REQUIRED")


def test_sec_authz_05_permissions_are_rechecked_after_role_change(client, auth, ids, owner_db):
    """Revocar la membresía surte efecto inmediato aunque el token siga vigente (no se cachean permisos en el JWT)."""
    h = auth("lector.alfa")
    assert client.get(f"/v1/cases/{ids['pago']}", headers=h).status_code == 200
    me = client.get("/v1/auth/me", headers=h).json()["id"]
    with owner_db.cursor() as cur:
        cur.execute("SELECT case_role FROM case_members WHERE case_id = %s AND user_id = %s", (ids["pago"], me))
        role = cur.fetchone()[0]
        cur.execute("DELETE FROM case_members WHERE case_id = %s AND user_id = %s", (ids["pago"], me))
    owner_db.commit()
    try:
        assert_error(client.get(f"/v1/cases/{ids['pago']}", headers=h), 404)
    finally:
        with owner_db.cursor() as cur:
            cur.execute("INSERT INTO case_members (case_id, user_id, organization_id, case_role) "
                        "SELECT %s, %s, organization_id, %s FROM cases WHERE id = %s", (ids["pago"], me, role, ids["pago"]))
        owner_db.commit()


def test_sec_authz_06_deactivated_user_token_stops_working(client, auth, owner_db, email):
    h = auth("gestor.alfa")
    assert client.get("/v1/auth/me", headers=h).status_code == 200
    with owner_db.cursor() as cur:
        cur.execute("UPDATE users SET is_active = false WHERE email = %s", (email("gestor.alfa"),))
    owner_db.commit()
    try:
        assert_error(client.get("/v1/auth/me", headers=h), 401, "AUTH_TOKEN_INVALID")
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE users SET is_active = true WHERE email = %s", (email("gestor.alfa"),))
        owner_db.commit()
