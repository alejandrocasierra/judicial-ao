"""Roles editables y su integración con el RBAC.

Los roles de sistema (ORG_ADMIN, LAWYER, …) se siembran desde `config/rbac.yaml`
y no se editan. Los roles personalizados se guardan en la tabla `roles` y sus
permisos se aplican en memoria (caché por organización) para no consultar la BD
en cada request.
"""
from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import rows
from app.security import rbac

_TTL_SECONDS = 30
_loaded: dict[str, float] = {}


def all_permissions() -> list[str]:
    return list(rbac.policy()["permissions"])


def ensure_builtin_roles(conn: Connection, org_id: str, user_id: str | None) -> None:
    """Siembra los roles del YAML como roles de sistema (idempotente)."""
    for code, perms in rbac.policy()["org_roles"].items():
        perm_list = sorted(rbac.policy()["permissions"]) if "*" in perms else sorted(perms)
        conn.execute(
            text("""INSERT INTO roles (organization_id, code, name, description, permissions, is_system, created_by)
                    VALUES (:o, :c, :n, :d, :p, true, :u)
                    ON CONFLICT (organization_id, code) DO NOTHING"""),
            {"o": org_id, "c": code, "n": code.replace("_", " ").title(),
             "d": "Rol de sistema", "p": perm_list, "u": user_id},
        )


def load_into_rbac(conn: Connection, org_id: str) -> None:
    """Carga los roles de la organización en la caché de RBAC (con TTL)."""
    now = time.monotonic()
    if org_id in _loaded and now - _loaded[org_id] < _TTL_SECONDS:
        return
    ensure_builtin_roles(conn, org_id, None)
    db_roles = rows(conn, "SELECT code, permissions FROM roles WHERE organization_id = :o", o=org_id)
    rbac.clear_org(org_id)
    if not db_roles:
        _loaded[org_id] = now
        return
    for r in db_roles:
        rbac.set_org_role(org_id, r["code"], set(r["permissions"] or []))
    _loaded[org_id] = now


def invalidate_cache(org_id: str) -> None:
    _loaded.pop(org_id, None)
    rbac.clear_org(org_id)
