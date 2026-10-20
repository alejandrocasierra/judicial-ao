"""Skills ejecutables de un agente (contrato §1.12 y §1.10).

Una skill aporta prompt, un allowlist de tools y parámetros por defecto. El
orden es determinista: `skills.position NULLS LAST, ordinality` (el orden de
enlace del agente como desempate), para que el mismo agente produzca el mismo
system prompt en cualquier modelo.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.engine import Connection

from app.core import db

log = logging.getLogger(__name__)


def _ids_array(skill_ids: list[str]) -> str:
    return "{" + ",".join(str(value) for value in skill_ids) + "}"


def _load_agent(conn: Connection, agent_id: str) -> dict | None:
    return db.one(conn, "SELECT system_prompt, skills FROM agents WHERE id = :i", i=str(agent_id))


def _load_skills(conn: Connection, skill_ids: list[str]) -> list[dict]:
    if not skill_ids:
        return []
    sql = """
        SELECT s.id, s.name, s.system_prompt, s.tools, s.parameters, s.position, u.ord
        FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS u(id, ord)
        JOIN skills s ON s.id = u.id
        ORDER BY s.position NULLS LAST, u.ord
    """
    try:
        return db.rows(conn, sql, ids=_ids_array(skill_ids))
    except Exception as exc:  # noqa: BLE001 — migración 0047 aún no aplicada
        log.debug("skills columns unavailable, falling back: %s", exc)
    try:
        return db.rows(conn, """
            SELECT s.id, s.name, s.system_prompt, '{}'::text[] AS tools,
                   '{}'::jsonb AS parameters, NULL::integer AS position, u.ord
            FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS u(id, ord)
            JOIN skills s ON s.id = u.id
            ORDER BY u.ord
        """, ids=_ids_array(skill_ids))
    except Exception as exc:  # noqa: BLE001 — ids inválidos: el agente sigue sin skills
        log.warning("skills could not be loaded: %s", exc)
        return []


def load_config(conn: Connection, agent_id: str | None) -> dict[str, Any]:
    """Configuración efectiva del agente: prompt fusionado, tools y parámetros."""
    default = {"prompt": None, "allowed_tools": None, "parameters": {}}
    if not agent_id:
        return default
    agent = _load_agent(conn, agent_id)
    if not agent:
        return default

    parts: list[str] = []
    if agent.get("system_prompt") and str(agent["system_prompt"]).strip():
        parts.append(str(agent["system_prompt"]).strip())

    skill_ids = [str(value) for value in (agent.get("skills") or []) if value]
    skills = _load_skills(conn, skill_ids)
    allowed: set[str] | None = None
    parameters: dict[str, Any] = {}
    for skill in skills:
        prompt = str(skill.get("system_prompt") or "").strip()
        if prompt:
            parts.append(f"[Skill: {skill.get('name') or 'skill'}]\n{prompt}")
        tools = skill.get("tools") or []
        if isinstance(tools, str):
            tools = [t for t in tools.strip("{}").split(",") if t]
        if tools:
            allowed = (allowed or set()) | {str(t).strip() for t in tools if str(t).strip()}
        params = skill.get("parameters") or {}
        if isinstance(params, dict):
            parameters.update(params)

    return {"prompt": "\n\n".join(parts) or None,
            "allowed_tools": allowed,
            "parameters": parameters}


def validate_tool(tool: str, allowed_tools: set[str] | None) -> tuple[bool, str | None]:
    """Valida el allowlist ANTES de ejecutar cualquier tool."""
    if not tool:
        return False, "missing tool"
    if allowed_tools is not None and tool not in allowed_tools:
        return False, f"tool '{tool}' is not allowed for this agent"
    try:
        from app.services import case_tools
        if tool not in case_tools.TOOL_REGISTRY:
            return False, f"unknown tool '{tool}'"
    except Exception:  # noqa: BLE001 — el registro puede no estar cargado aún
        log.debug("tool registry unavailable", exc_info=True)
    return True, None


def register(_settings=None) -> None:
    """Compatibilidad: las skills viven en BD, no se registran en proceso."""
    return None


def available_tools() -> list[str]:
    try:
        from app.services import case_tools
        return case_tools.tool_names()
    except Exception:  # noqa: BLE001
        return []


def config_for_org(conn: Connection, agent_id: str | None) -> dict[str, Any]:
    """Alias explícito para llamadores que quieren leer la config sin prompt."""
    return load_config(conn, agent_id)
