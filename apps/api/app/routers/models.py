"""GET /v1/models — modelos IA disponibles para consultas.
GET /v1/agents — agentes disponibles para el chat (lectura, sin permiso de administración)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.db import rows, tx
from app.security.deps import Principal, require_org
from app.services import builtin_agents

router = APIRouter(prefix="/models", tags=["ai"])
agents_router = APIRouter(prefix="/agents", tags=["ai"])


@router.get("")
@router.get("/")
def list_models(p: Principal = Depends(require_org("ai.query"))):
    """Modelos IA configurados en la organización, visibles para usuarios que pueden consultar."""
    with tx(p.org_id, p.user_id) as c:
        return rows(
            c,
            """SELECT id, provider, model_name, is_default, created_at, updated_at,
                      (encrypted_api_key IS NOT NULL) AS has_api_key
               FROM ai_models
               ORDER BY is_default DESC, provider, model_name""",
        )


@agents_router.get("")
@agents_router.get("/")
def list_agents(p: Principal = Depends(require_org("ai.query"))):
    """Agentes configurados, para elegir con "/" en el chat. Solo lectura:
    cualquier usuario con ai.query puede verlos (la gestión sigue en /admin/agents)."""
    with tx(p.org_id, p.user_id) as c:
        # Siembra automática (idempotente) para que el chat tenga agentes listos
        # aunque nadie haya abierto el panel de administración todavía.
        builtin_agents.ensure_chat_agents(c, p.org_id, p.user_id)
        return rows(c, """SELECT id, name, is_system, kind, created_at FROM agents
                          ORDER BY is_system DESC, kind, name""")
