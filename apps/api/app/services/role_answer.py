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
    confirmed = any(it.get("confirmed") for it in items)
    authorities = bool(items) and all(it.get("authority") for it in items)
    for it in items:
        n = it.get("person_name") or it.get("display_name") or it.get("label")
        if not n:
            continue
        if it.get("authority"):
            names.append(f"- {n} — {it.get('mentions') or 0} actuaciones")
        elif it.get("confirmed"):
            extra = it.get("speaker_role") or ""
            party = f" · parte: {it.get('party_name')}" if it.get("party_name") else ""
            names.append(f"- {n}" + (f" ({extra})" if extra else "") + party + f" — {it.get('segments') or 0} segmentos")
        else:
            names.append(f"- {n} — {it.get('mentions')} menciones; {it.get('filename')} p.{it.get('page_number')}")
    if not names:
        return None
    if authorities:
        header = (f"LISTA de autoridades judiciales detectadas en el proceso para «{role}» (normalizadas; "
                  f"{len(names)} despachos detectados; puede haber variantes de OCR). Responde enumerando estas "
                  "autoridades (destaca la principal por nº de actuaciones) y aclara que el total es aproximado; "
                  "no contestes con un fragmento de transcripción.")
    elif confirmed:
        header = (f"LISTA OFICIAL de «{role}» (roles CONFIRMADOS por el usuario; TOTAL={len(names)}). "
                  "Responde con EXACTAMENTE estos nombres y este total (con su parte si la tienen). No añadas ni "
                  "quites personas ni la contestes con un fragmento de transcripción.")
    else:
        header = (f"LISTA DETERMINISTA de «{role}» (candidatos detectados en firmas; TOTAL={len(names)}), "
                  "la misma para cualquier modelo. Responde con ESTA lista: nombres DISTINTOS, total y cita cada "
                  "uno; aclara que es heurística y que conviene verificarla.")
    return header + "\n" + "\n".join(names)
