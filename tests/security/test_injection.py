"""SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5.
El código usa exclusivamente parámetros enlazados; estas pruebas lo confirman desde fuera."""
from __future__ import annotations

import pytest

from helpers import assert_error, create_case, fetch

SQLI = [
    "' OR '1'='1",
    "'; DROP TABLE cases; --",
    "\" OR 1=1 --",
    "1; SELECT pg_sleep(5)--",
    "' UNION SELECT password_hash FROM users --",
    "%' AND 1=CAST((SELECT current_user) AS int) --",
    "$$; DELETE FROM audit_logs; $$",
]


@pytest.mark.parametrize("payload", SQLI)
def test_sec_inj_01_sqli_in_login_is_inert(client, payload, password):
    r = client.post("/v1/auth/login", json={"email": payload, "password": payload})
    assert r.status_code in (401, 422) and "access_token" not in r.text


@pytest.mark.parametrize("payload", SQLI)
def test_sec_inj_02_sqli_in_case_title_is_stored_literally(client, auth, payload):
    case = create_case(client, auth("abogada.alfa"), title=f"Caso {payload}")
    got = client.get(f"/v1/cases/{case['id']}", headers=auth("abogada.alfa")).json()
    assert got["title"] == f"Caso {payload}"


@pytest.mark.parametrize("payload", SQLI)
def test_sec_inj_03_sqli_in_question_and_filters(client, auth, ids, payload):
    r = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"),
                    json={"question": f"pago {payload}", "mode": "fact_lookup"})
    assert r.status_code == 200
    r = client.get("/v1/audit", headers=auth("admin.alfa"), params={"action": payload[:100]})
    assert r.status_code == 200 and r.json() == []


def test_sec_inj_04_database_intact_after_payloads(owner_db):
    tables = {t for (t,) in fetch(owner_db, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert {"cases", "users", "audit_logs", "documents"} <= tables
    assert fetch(owner_db, "SELECT count(*) FROM users")[0][0] > 0


@pytest.mark.parametrize("bad", ["1", "abc", "1 OR 1=1", "../../etc/passwd", "00000000-0000-0000-0000-00000000000g"])
def test_sec_inj_05_path_ids_must_be_uuids(client, auth, bad):
    # 422 si llega al router; 404 si la normalización de la URL lo saca de la ruta. Nunca 2xx/5xx.
    r = client.get(f"/v1/cases/{bad}", headers=auth("admin.alfa"))
    assert_error(r, r.status_code)
    assert r.status_code in (404, 422)


@pytest.mark.parametrize("field,value", [("title", "Título\x00con nulo"), ("title", "Salto\x1bescape"),
                                         ("case_number", "123\x07")])
def test_sec_inj_06_control_characters_rejected(client, auth, field, value):
    body = {"jurisdiction": "co", "case_number": "11001310300120240099900", "title": "Título válido", "language": "es"}
    body[field] = value
    assert_error(client.post("/v1/cases", headers=auth("abogada.alfa"), json=body), 422)


def test_sec_inj_07_question_length_limit(client, auth, ids, settings):
    r = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"),
                    json={"question": "a" * (settings.QUESTION_MAX_CHARS + 1), "mode": "fact_lookup"})
    assert_error(r, 422, "QUESTION_TOO_LONG")


def test_sec_inj_08_validation_errors_do_not_echo_input(client, auth):
    probe = "<script>alert('xss-probe')</script>"
    r = client.post("/v1/cases", headers=auth("abogada.alfa"), json={
        "jurisdiction": probe, "case_number": probe, "title": "ok título", "language": probe})
    assert r.status_code == 422 and "xss-probe" not in r.text


def test_sec_inj_09_malformed_json_is_422_not_500(client, auth):
    r = client.post("/v1/cases", headers={**auth("abogada.alfa"), "Content-Type": "application/json"}, content=b"{not json")
    assert_error(r, 422, "VALIDATION_ERROR")
