"""Servidor MCP (streamable HTTP) del Chat IA.

Expone las tools de `app/services/case_tools/` (Fase 1) como tools MCP
estándar — usable desde Claude Code, Cursor u otro cliente MCP — y recursos
`case://` del expediente. No duplica lógica: cada tool MCP es un wrapper
fino sobre case_tools.execute() con:

- mismo JWT de la plataforma (JwtAuthMiddleware),
- allowlist por rol vía RBAC/case_access (lectura: ai.query; correcciones:
  document.upload / media.upload, igual que los endpoints REST),
- tx(org_id, actor_id) por llamada → RLS multi-tenant,
- propagación post-commit (pgvector/grafo/jobs) tras confirmar la transacción.
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.security.deps import Principal, case_access
from app.services import case_tools
from app.services.case_tools import ToolContext

from mcp_server.auth import JwtAuthMiddleware, current_principal

log = logging.getLogger(__name__)

# Allowlist por rol: las tools de lectura exigen el mismo permiso que el chat;
# las de escritura exigen el permiso del endpoint REST equivalente.
READ_PERMISSION = "ai.query"
WRITE_PERMISSION_BY_TOOL = {
    "correct_ocr_page": "document.upload",
    "correct_transcript_segment": "media.upload",
}


def _required_permission(tool: str, arguments: dict[str, Any]) -> str:
    if tool in WRITE_PERMISSION_BY_TOOL:
        return WRITE_PERMISSION_BY_TOOL[tool]
    if tool == "suggest_reprocess":
        return "media.upload" if arguments.get("media_id") else "document.upload"
    return READ_PERMISSION


def _check_access(p: Principal, case_id: str, permission: str) -> None:
    try:
        case_access(p, case_id, permission)
    except AppError as e:
        # 404 sin revelar existencia (otro tenant) / 403 sin permiso — mismo contrato que la API
        raise McpError(ErrorData(code=e.status, message=e.code)) from None


def _run_tool(name: str, case_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta una case_tool con el Principal de la request: RBAC → tx(RLS) → post-commit."""
    p = current_principal()
    _check_access(p, case_id, _required_permission(name, arguments))
    ctx = ToolContext(org_id=p.org_id, actor_id=p.user_id, locale=p.locale)
    with tx(p.org_id, p.user_id) as conn:
        items = case_tools.execute(conn, case_id, name, arguments, ctx=ctx)
    case_tools.drain_post_commit(ctx)
    return {"items": items}


_ANNOTATIONS = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}


def _register_tools(mcp: FastMCP) -> None:
    """Registra cada case_tool como tool MCP con su esquema de parámetros tipado."""
    for name, spec in case_tools.TOOL_REGISTRY.items():
        # Los defaults reales del handler (no del esquema declarativo): un parámetro
        # sin default en el handler es obligatorio; con default es opcional.
        handler_sig = inspect.signature(spec.handler)
        parameters = [inspect.Parameter("case_id", inspect.Parameter.KEYWORD_ONLY, annotation=str)]
        for pname, pschema in spec.parameters.items():
            hparam = handler_sig.parameters.get(pname)
            default = hparam.default if hparam is not None else pschema.get("default", inspect.Parameter.empty)
            parameters.append(inspect.Parameter(
                pname, inspect.Parameter.KEYWORD_ONLY,
                annotation=_ANNOTATIONS.get(pschema.get("type"), str),
                default=default))

        def _make(tool_name: str) -> Any:
            async def mcp_tool_fn(**kwargs: Any) -> dict[str, Any]:
                case_id = kwargs.pop("case_id")
                return _run_tool(tool_name, case_id, kwargs)
            return mcp_tool_fn

        tool_handler = _make(name)
        tool_handler.__name__ = name
        tool_handler.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
        description = spec.description
        if spec.kind == "write":
            description += (" [ESCRITURA — protocolo de confirmación: primero llama sin 'confirm' "
                            "para obtener la vista previa; solo con la confirmación explícita del "
                            "usuario vuelve a llamar con confirm=true]")
        mcp.add_tool(tool_handler, name=name, description=f"[{spec.kind}] {description}")


def _register_resources(mcp: FastMCP) -> None:
    """Recursos de lectura directa del expediente (mismo control de acceso)."""

    @mcp.resource("case://{case_id}/files", mime_type="application/json")
    def case_files(case_id: str) -> str:
        """Archivos del expediente (documentos y media) con id, nombre y tipo."""
        p = current_principal()
        _check_access(p, case_id, "case.read")
        with tx(p.org_id, p.user_id) as conn:
            docs = rows(conn, """SELECT id, filename, mime_type, size_bytes, page_count, ocr_mode,
                                   processing_status, created_at FROM documents WHERE case_id = :c
                                   ORDER BY filename""", c=case_id)
            media = rows(conn, """SELECT id, filename, title, media_type, mime_type, size_bytes,
                                    duration_ms, processing_status, created_at FROM media
                                    WHERE case_id = :c ORDER BY filename""", c=case_id)
        return json.dumps({"documents": docs, "media": media}, default=str, ensure_ascii=False)

    @mcp.resource("case://{case_id}/graph/stats", mime_type="application/json")
    def graph_stats(case_id: str) -> str:
        """Estadísticas del knowledge graph: nodos y aristas por tipo."""
        p = current_principal()
        _check_access(p, case_id, "case.read")
        with tx(p.org_id, p.user_id) as conn:
            nodes = rows(conn, """SELECT node_type, count(*) AS n FROM graph_nodes
                                   WHERE case_id = :c GROUP BY node_type ORDER BY n DESC""", c=case_id)
            edges = rows(conn, """SELECT edge_type, count(*) AS n FROM graph_edges
                                   WHERE case_id = :c GROUP BY edge_type ORDER BY n DESC""", c=case_id)
        return json.dumps({"nodes_by_type": nodes, "edges_by_type": edges}, default=str, ensure_ascii=False)

    @mcp.resource("case://{case_id}/doc/{document_id}/page/{page_number}")
    def doc_page(case_id: str, document_id: str, page_number: str) -> str:
        """Texto OCR de una página de un documento del expediente."""
        p = current_principal()
        _check_access(p, case_id, "document.read")
        with tx(p.org_id, p.user_id) as conn:
            page = one(conn, """SELECT p.text FROM document_pages p
                JOIN documents d ON d.id = p.document_id
                WHERE d.case_id = :c AND p.document_id = :d AND p.page_number = :n""",
                       c=case_id, d=document_id, n=int(page_number))
        if not page:
            raise McpError(ErrorData(code=404, message="DOCUMENT_NOT_FOUND"))
        return page["text"] or ""


def create_app() -> Any:
    """ASGI app lista para uvicorn: FastMCP streamable HTTP + JWT."""
    s = get_settings()
    allowed_hosts = {h.strip() for h in s.MCP_ALLOWED_HOSTS.split(",") if h.strip()}
    allowed_hosts |= {f"{s.MCP_SERVER_HOST}:{s.MCP_SERVER_PORT}", "localhost", "127.0.0.1"}
    mcp = FastMCP("judicial-ai", instructions=(
        "Servidor MCP del expediente judicial. Responde SOLO con información del caso "
        "accedida mediante estas tools/recursos; nunca uses conocimiento externo. "
        "Las tools de escritura (correcciones) exigen confirmación explícita del usuario."
    ), transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=sorted(allowed_hosts)))
    _register_tools(mcp)
    _register_resources(mcp)
    return JwtAuthMiddleware(mcp.streamable_http_app())


# uvicorn mcp_server.server:app
app = create_app()
