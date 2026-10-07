"""Roles de parte POR CASO (catálogo editable).

Cada expediente tiene su propia lista de roles (Demandante, Demandado, Apoderado, …).
Se siembran valores por defecto la primera vez y el usuario puede crear, renombrar o
eliminar roles. `parties.role` guarda el `code` del rol.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import rows

# (code, label, orden). Codes compatibles con el histórico (claimant/defendant/…).
DEFAULT_ROLES: list[tuple[str, str, int]] = [
    ("claimant", "Demandante", 10),
    ("defendant", "Demandado", 20),
    ("plaintiff", "Ejecutante", 30),
    ("respondent", "Ejecutado", 40),
    ("attorney", "Apoderado", 50),
    ("representative", "Representante", 60),
    ("third_party", "Tercero", 70),
    ("witness", "Testigo", 80),
    ("expert", "Perito", 90),
    ("judge", "Juez", 100),
]


def slugify(label: str) -> str:
    s = unicodedata.normalize("NFKD", (label or "").strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return (s or "rol")[:60]


def ensure_defaults(conn: Connection, org_id: str, case_id: str) -> None:
    """Siembra los roles por defecto si el caso aún no tiene catálogo."""
    n = conn.execute(text("SELECT count(*) FROM party_roles WHERE case_id = :c"), {"c": case_id}).scalar()
    if n:
        return
    for code, label, order in DEFAULT_ROLES:
        conn.execute(text("""
            INSERT INTO party_roles (organization_id, case_id, code, label, is_system, sort_order)
            VALUES (:o, :c, :code, :label, true, :ord)
            ON CONFLICT (case_id, code) DO NOTHING
        """), {"o": org_id, "c": case_id, "code": code, "label": label, "ord": order})


def list_roles(conn: Connection, case_id: str) -> list[dict[str, Any]]:
    return rows(conn, """SELECT id, code, label, is_system, sort_order
                         FROM party_roles WHERE case_id = :c
                         ORDER BY sort_order, label""", c=case_id)


def create_role(conn: Connection, org_id: str, case_id: str, label: str, code: str | None = None) -> dict[str, Any]:
    code = (code or slugify(label)).strip() or slugify(label)
    return dict(conn.execute(text("""
        INSERT INTO party_roles (organization_id, case_id, code, label, is_system, sort_order)
        VALUES (:o, :c, :code, :label, false, 100)
        RETURNING id, code, label, is_system, sort_order
    """), {"o": org_id, "c": case_id, "code": code, "label": label.strip()}).mappings().first())


def role_in_use(conn: Connection, case_id: str, code: str) -> int:
    return int(conn.execute(text("SELECT count(*) FROM parties WHERE case_id = :c AND role = :r"),
                              {"c": case_id, "r": code}).scalar() or 0)
