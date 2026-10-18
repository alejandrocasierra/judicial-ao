"""Relación entre el `actor` textual de un evento procesal y los roles de parte.

La línea de tiempo procesal etiqueta cada actuación con un `actor` canónico
(juzgado | demandante | demandado | apoderado_demandante | ...). Este módulo
mapea esas etiquetas a los códigos de rol de `parties.role` para poder vincular
la actuación con las partes reales del proceso.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy.engine import Connection

from app.core.db import rows

# actor canónico del prompt extract_procedural_events -> código de rol en parties.
ACTOR_ROLE_MAP: dict[str, str | None] = {
    "demandante": "claimant",
    "demandado": "defendant",
    "apoderado_demandante": "attorney_claimant",
    "apoderado_demandado": "attorney_defendant",
    "tercero": "third_party",
    "fiscal": None,
    "juzgado": None,
    "secretario": None,
    "otro": None,
}


def role_code_for_actor(actor: str | None) -> str | None:
    """Devuelve el código de rol de parte asociado a un actor textual, si existe."""
    if not actor:
        return None
    return ACTOR_ROLE_MAP.get(actor.lower().strip())


def parties_by_role(conn: Connection, case_id: str) -> dict[str, list[str]]:
    """Partes del caso indexadas por código de rol."""
    out: dict[str, list[str]] = defaultdict(list)
    for r in rows(conn, "SELECT id, role FROM parties WHERE case_id = :c", c=case_id):
        out[r["role"]].append(str(r["id"]))
    return out


def resolve_actor_parties(conn: Connection, case_id: str, actor: str | None) -> list[str]:
    """UUIDs de las partes del caso que tienen el rol asociado al actor textual."""
    role = role_code_for_actor(actor)
    if not role:
        return []
    return parties_by_role(conn, case_id).get(role, [])
