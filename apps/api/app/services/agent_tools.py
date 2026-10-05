"""Tools allowlist para el agente jurídico (SSD §49, §98).

Compatibilidad: la implementación vive en `app/services/case_tools/` (capa
compartida con el servidor MCP). Este módulo conserva la API histórica
(`execute(conn, case_id, tool, args)`, `reset_handles`, `TOOL_DESCRIPTIONS`,
`_ilike_terms`, `answering`) usada por el loop del agente y por las pruebas.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Connection

from app.services import answering  # noqa: F401  (re-exportado: las pruebas parchean agent_tools.answering)
from app.services import case_tools
from app.services.case_tools import TOOL_DESCRIPTIONS, reset_handles  # noqa: F401
from app.services.case_tools.read import _ilike_terms  # noqa: F401  (usado en tests unitarios)


def execute(conn: Connection, case_id: str, tool: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Punto de entrada del loop del agente: usa el ToolContext fijado por run_agent_query."""
    return case_tools.execute(conn, case_id, tool, arguments, ctx=case_tools.get_context())
