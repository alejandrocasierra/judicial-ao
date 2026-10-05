"""Utilidades compartidas por las suites. Nada de datos de negocio quemados:
los valores de prueba se generan (uuid) o vienen de las semillas/entorno."""
from __future__ import annotations

import uuid

import psycopg


def new_case_number() -> str:
    """Radicado colombiano sintético de 23 dígitos, único por llamada."""
    return str(uuid.uuid4().int)[:23].ljust(23, "0")


def create_case(client, headers: dict, title: str | None = None, language: str = "es",
                case_number: str | None = None) -> dict:
    r = client.post("/v1/cases", headers=headers, json={
        "jurisdiction": "co", "case_number": case_number or new_case_number(),
        "title": title or f"Caso de prueba {uuid.uuid4().hex[:8]}", "language": language})
    assert r.status_code == 201, r.text
    return r.json()


def assert_error(r, status: int, code: str | None = None) -> dict:
    """Verifica el contrato de error §118: {error:{code,message,request_id}} y sin stack traces."""
    assert r.status_code == status, f"{r.status_code} != {status}: {r.text}"
    body = r.json()
    assert set(body) == {"error"}, body
    err = body["error"]
    assert err["code"] and err["message"] and err["request_id"]
    if code:
        assert err["code"] == code, err
    assert "Traceback" not in r.text and "File \"" not in r.text
    return err


def db_raises(conn, sql: str, params: tuple = (), org: str | None = None) -> str:
    """Ejecuta SQL que DEBE fallar; devuelve el mensaje de error. Deja la conexión usable."""
    try:
        with conn.cursor() as cur:
            if org is not None:
                cur.execute("SELECT set_config('app.current_org', %s, true)", (org,))
            cur.execute(sql, params)
    except psycopg.Error as e:
        conn.rollback()
        return str(e)
    conn.rollback()
    raise AssertionError(f"Se esperaba error de base de datos y no ocurrió: {sql}")


def fetch(conn, sql: str, params: tuple = (), org: str | None = None) -> list[tuple]:
    with conn.cursor() as cur:
        if org is not None:
            cur.execute("SELECT set_config('app.current_org', %s, true)", (org,))
        cur.execute(sql, params)
        out = cur.fetchall() if cur.description else []
    conn.rollback()
    return out
