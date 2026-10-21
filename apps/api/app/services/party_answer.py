"""Respuesta determinista con la ficha de partes del proceso.

La tabla `parties` es la fuente autoritativa de los roles (juez, demandante,
demandado, apoderado, tercero...). Las preguntas de rol no deben responderse con
menciones sueltas de páginas: eso producía listas como «Sujetos Procesales» o
«Mayor Cuantía» como si fueran demandados.
"""
from __future__ import annotations

import re

from sqlalchemy.engine import Connection

from app.core.db import rows
from app.services import text_terms

ROLE_LABELS = {
    "claimant": "Demandante",
    "defendant": "Demandado",
    "judge": "Juez",
    "magistrado": "Magistrado",
    "attorney_claimant": "Apoderado de la parte demandante",
    "attorney_defendant": "Apoderado de la parte demandada",
    "representative": "Representante legal",
    "third_party": "Tercero",
}

_PARTIES_GENERIC = re.compile(
    r"\b(partes|intervinientes|sujetos procesales|roles)\b",
    re.I,
)
_APODERADO = re.compile(r"\bapoderad[oa]s?\b|\babogad[oa]s?\b", re.I)
_DEMANDANTE = re.compile(r"\bdemandante?s?\b|\bejecutante?s?\b", re.I)
_DEMANDADO = re.compile(r"\bdemandad[oa]s?\b|\bejecutad[oa]s?\b", re.I)
_JUEZ = re.compile(r"\bjue(?:z|ces|za|zas)\b", re.I)
_MAGISTRADO = re.compile(r"\bmagistrad[oa]s?\b", re.I)
_TERCERO = re.compile(r"\btercer[oa]s?\b", re.I)
_REPRESENTANTE = re.compile(r"\brepresentante(?:s)? legal(?:es)?\b", re.I)
_IDENTITY = re.compile(
    r"\b(quienes son|quien es|cual es el nombre|cuales son los nombres|cuantos|"
    r"cuantas|nombre de|integrantes|lista de|dime los|dime las)\b", re.I)


def requested_roles(question: str) -> list[str] | None:
    text = question or ""
    if _APODERADO.search(text):
        return ["attorney_claimant", "attorney_defendant"]
    if _DEMANDANTE.search(text):
        return ["claimant"]
    if _DEMANDADO.search(text):
        return ["defendant"]
    if _MAGISTRADO.search(text):
        return ["magistrado"]
    if _JUEZ.search(text):
        return ["judge", "magistrado"]
    if _TERCERO.search(text):
        return ["third_party"]
    if _REPRESENTANTE.search(text):
        return ["representative"]
    if _PARTIES_GENERIC.search(text):
        return []
    return None


def is_identity_question(question: str) -> bool:
    """Distinguir «quiénes son los demandados» de «qué dice la parte demandada»."""
    if requested_roles(question) is None:
        return False
    return bool(_IDENTITY.search(text_terms.norm(question)))


def _label(role: str) -> str:
    return ROLE_LABELS.get(role, role.replace("_", " ").capitalize())


def render_for_question(conn: Connection, case_id: str, question: str) -> str | None:
    if not is_identity_question(question):
        return None
    roles = requested_roles(question)
    if roles is None:
        return None
    found = rows(conn, """
        SELECT name, role, entity_type
          FROM parties
         WHERE case_id = :c
         ORDER BY role, name
    """, c=case_id)
    if roles:
        found = [row for row in found if str(row.get("role")) in roles]
    if not found:
        return None
    # Deduplicar por (rol, nombre normalizado).
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for row in found:
        key = (str(row.get("role")), text_terms.norm(str(row.get("name") or "")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    grouped: dict[str, list[dict]] = {}
    for row in unique:
        grouped.setdefault(str(row.get("role")), []).append(row)
    total = len(unique)
    header = (f"Según la ficha de partes del proceso ({total} registro"
              f"{'s' if total != 1 else ''}):")
    lines = [header]
    for role in sorted(grouped, key=lambda value: _label(value)):
        names = []
        for row in grouped[role]:
            name = str(row.get("name") or "").strip()
            if name:
                names.append(name)
        if names:
            lines.append(f"- **{_label(role)}**: " + "; ".join(names))
    lines.append("")
    lines.append("Fuente: ficha de partes del proceso (no es una cita documental).")
    return "\n".join(lines)


__all__ = ["render_for_question", "requested_roles", "is_identity_question"]
