"""Respuesta DETERMINISTA para preguntas de rol/agregación («¿cuántos jueces han intervenido?»).

El agente redacta la respuesta; para que NO varíe según el modelo, aquí calculamos una vez la
lista de candidatos por rol (list_people_by_role) y se la damos al modelo como fuente fija.
"""
from __future__ import annotations

from app.core.db import tx
from app.services import case_tools
from app.services.case_tools import ToolContext
from app.services.query_hints import role_from_text


def authoritative_hint(org_id: str, user_id: str | None, case_id: str, question: str) -> str | None:
    """Bloque de texto con la lista fija de candidatos del rol pedido, o None si no aplica."""
    role = role_from_text(question)
    if not role:
        return None
    try:
        with tx(org_id, user_id) as c:
            items = case_tools.execute(c, str(case_id), "list_people_by_role", {"role": role, "k": 20},
                                       ctx=ToolContext(org_id=org_id, actor_id=user_id))
    except Exception:  # noqa: BLE001 — nunca romper la consulta por esto
        return None
    names: list[str] = []
    for it in items:
        n = it.get("person_name")
        if not n:
            continue
        names.append(f"- {n} — {it.get('mentions')} menciones; {it.get('filename')} p.{it.get('page_number')}")
    if not names:
        return None
    return (
        f"LISTA DETERMINISTA de «{role}» (candidatos detectados en firmas; TOTAL={len(names)}), "
        "la misma para cualquier modelo. Para «¿quiénes…?» o «¿cuántos…?» responde con ESTA lista: "
        "enumera estos nombres DISTINTOS, indica el total y cita cada uno (documento/página). NO añadas ni "
        "quites personas de la lista; aclara que es un resultado heurístico y que debe verificarse.\n"
        + "\n".join(names)
    )
