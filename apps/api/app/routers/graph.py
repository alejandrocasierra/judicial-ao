"""Endpoints para navegar el Knowledge Graph y generar matrices de evidencia."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.db import tx
from app.schemas import Strict
from app.security.deps import Principal, case_access, current_principal
from app.services import audit, graph, ratelimit

router = APIRouter(prefix="/cases/{case_id}", tags=["graph"])


class _GraphBuildIn(Strict):
    pass


@router.post("/graph/build")
def build_graph(case_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    ratelimit.check("upload", p.user_id)
    case_access(p, case_id, "ai.graph")
    with tx(p.org_id, p.user_id) as conn:
        result = graph.build_case_graph(conn, str(p.org_id), str(case_id), str(p.user_id))
        audit.record(conn, org_id=p.org_id, actor_id=p.user_id, action="graph.build",
                     entity_type="case", entity_id=str(case_id),
                     after=result, request=request)
    return result


@router.get("/graph/nodes")
def list_nodes(case_id: UUID, node_type: str | None = None, q: str | None = None, limit: int = 20,
               p: Principal = Depends(current_principal)):
    case_access(p, case_id, "ai.graph")
    with tx(p.org_id, p.user_id) as conn:
        items = graph.find_node(conn, str(case_id), node_type=node_type, query=q, limit=limit)
    return {"nodes": items}


@router.get("/graph/traverse/{node_id}")
def traverse(case_id: UUID, node_id: UUID, depth: int = 2,
             edge_type: str | None = None, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "ai.graph")
    edge_types = [edge_type] if edge_type else None
    with tx(p.org_id, p.user_id) as conn:
        result = graph.traverse(conn, str(case_id), str(node_id), depth=depth, edge_types=edge_types)
    return result


@router.get("/graph/evidence-matrix")
def evidence_matrix(case_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "ai.graph")
    with tx(p.org_id, p.user_id) as conn:
        result = graph.evidence_matrix(conn, str(case_id))
    return result
