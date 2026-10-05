"""Capa de tools del caso (Chat IA + MCP): una sola implementación compartida.

Cada tool declara nombre, descripción, esquema de parámetros y tipo (read/write),
y devuelve ítems de evidencia estructurados con un handle único (E1, P1, TR1…)
para que el agente pueda citarlos. Las tools de ESCRITURA (correcciones) siguen
el protocolo de confirmación: sin `confirm=true` devuelven una vista previa del
cambio; con `confirm=true` lo ejecutan y propagan a BD → pgvector → grafo,
dejando registro en `reviews` y auditoría.

Consumidores:
- El loop del agente interno (`app/services/agent.py` vía `agent_tools`).
- El servidor MCP (Fase 2) llamando `execute()` con un `ToolContext` explícito.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.engine import Connection

# ---------------------------------------------------------------------------
# Contexto de ejecución (org/actor/locale/adjuntos). El agente interno lo fija
# con set_context() antes del loop; el servidor MCP lo pasa explícito a execute().
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    org_id: str | None = None
    actor_id: str | None = None
    locale: str = "es"
    attachments: list[dict[str, Any]] = field(default_factory=list)
    # Acciones diferidas (reindex pgvector, rebuild del grafo, encolado de jobs).
    # Se ejecutan DESPUÉS de que el llamador confirme su transacción (ver drain_post_commit).
    post_commit: list[Callable[[], None]] = field(default_factory=list)


def drain_actions(actions: list[Callable[[], None]]) -> None:
    """Ejecuta acciones diferidas (ver drain_post_commit). Nunca lanza: registra y sigue."""
    import logging
    log = logging.getLogger(__name__)
    for fn in actions:
        try:
            fn()
        except Exception:
            log.exception("post_commit action failed: %s", fn)


def drain_post_commit(ctx: ToolContext) -> None:
    """Ejecuta y limpia las acciones diferidas del contexto.

    DEBE llamarse después de confirmar la transacción del llamador. Mientras esa
    transacción sigue abierta, una segunda conexión se auto-bloquea: las filas
    recién insertadas tienen row locks sin confirmar y el trigger audit_chain
    retiene un pg_advisory_xact_lock (deadlock entre dos conexiones del mismo flujo).
    """
    actions, ctx.post_commit = list(ctx.post_commit), []
    drain_actions(actions)


_ctx_local = threading.local()


def set_context(ctx: ToolContext | None) -> None:
    _ctx_local.ctx = ctx


def get_context() -> ToolContext:
    return getattr(_ctx_local, "ctx", None) or ToolContext()


# ---------------------------------------------------------------------------
# Handles de evidencia (thread-local: dos peticiones concurrentes no colisionan)
# ---------------------------------------------------------------------------

_handle_local = threading.local()


def _next_handle(prefix: str) -> str:
    n = getattr(_handle_local, "n", 0) + 1
    _handle_local.n = n
    return f"{prefix}{n}"


def reset_handles() -> None:
    _handle_local.n = 0


def evidence_item(prefix: str, source_type: str, snippet: str, **kwargs: Any) -> dict[str, Any]:
    item = {"handle": _next_handle(prefix), "source_type": source_type, "text": snippet or ""}
    item.update(kwargs)
    return item


# ---------------------------------------------------------------------------
# Registro de tools
# ---------------------------------------------------------------------------

Handler = Callable[..., list[dict[str, Any]]]


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]
    kind: str  # "read" | "write"
    handler: Handler


TOOL_REGISTRY: dict[str, ToolDef] = {}


def register(name: str, description: str, parameters: dict[str, Any], kind: str = "read"):
    def deco(fn: Handler) -> Handler:
        TOOL_REGISTRY[name] = ToolDef(name=name, description=description, parameters=parameters,
                                      kind=kind, handler=fn)
        return fn
    return deco


# Descripciones en el formato histórico usado por builtin_agents.tool_names().
TOOL_DESCRIPTIONS: dict[str, dict[str, Any]] = {}


def execute(conn: Connection, case_id: str, tool: str, arguments: dict[str, Any],
            ctx: ToolContext | None = None) -> list[dict[str, Any]]:
    ctx = ctx or get_context()
    spec = TOOL_REGISTRY.get(tool)
    if spec is None:
        raise ValueError(f"unknown tool: {tool}")
    return spec.handler(conn, case_id, ctx, **dict(arguments or {}))


def tool_names() -> list[str]:
    return sorted(TOOL_REGISTRY.keys())


# Las implementaciones se registran al importar los submódulos.
from app.services.case_tools import read as _read  # noqa: E402,F401
from app.services.case_tools import write as _write  # noqa: E402,F401

TOOL_DESCRIPTIONS.update({name: {"description": spec.description, "parameters": spec.parameters}
                          for name, spec in TOOL_REGISTRY.items()})
