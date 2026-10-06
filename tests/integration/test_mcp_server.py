"""IT-MCP — servidor MCP real (streamable HTTP + JWT + RLS + allowlist por rol).

Cliente MCP oficial contra la ASGI app en-proceso (LifespanManager + ASGITransport):
sin servidor de red, pero con todo el stack (middleware JWT, FastMCP, tools, RBAC).
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from asgi_lifespan import LifespanManager
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

pytestmark = pytest.mark.integration

PLAN_TOOLS = {"search_case", "read_document", "get_document_page", "get_document_markdown",
              "search_transcript_by_time", "get_file", "list_case_files", "graph_query", "graph_neighbors",
              "find_person", "get_timeline", "get_video_segment", "search_transcripts", "list_speakers",
              "list_people_by_role", "correct_ocr_page", "correct_transcript_segment", "rename_speaker",
              "merge_speakers", "suggest_reprocess", "list_low_confidence_pages", "reprocess_low_confidence",
              "event_relations", "process_path"}


@asynccontextmanager
async def _mcp_session(bearer_token: str):
    """App con lifespan + cliente MCP oficial por ASGI (sin servidor de red)."""
    from mcp_server.server import create_app
    app = create_app()
    async with LifespanManager(app):
        def factory(headers=None, timeout=None, auth=None, **kw):
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost", headers=headers)
        async with streamablehttp_client("http://localhost/mcp",
                                         headers={"Authorization": f"Bearer {bearer_token}"},
                                         httpx_client_factory=factory) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def _token(auth_headers: dict) -> str:
    return auth_headers["Authorization"].split(" ", 1)[1]


def _run(coro):
    return asyncio.run(coro)


def _items(result) -> list[dict]:
    assert not result.isError, result.content[0].text if result.content else "error"
    data = json.loads(result.content[0].text)
    return data["items"]


def test_it_mcp_01_tools_list_exposes_case_tools(client, auth):
    async def go():
        async with _mcp_session(_token(auth("abogada.alfa"))) as session:
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert PLAN_TOOLS <= names, PLAN_TOOLS - names
            # las de escritura declaran el protocolo de confirmación
            corr = next(t for t in tools.tools if t.name == "correct_ocr_page")
            assert "confirm" in (corr.description or "")
    _run(go())


def test_it_mcp_02_list_case_files_and_get_file(client, auth, ids):
    async def go():
        async with _mcp_session(_token(auth("abogada.alfa"))) as session:
            items = _items(await session.call_tool("list_case_files", {"case_id": ids["pago"]}))
            assert any(it["source_type"] == "file" and it.get("download_path") for it in items)
            cards = _items(await session.call_tool("get_file", {"case_id": ids["pago"], "query": "demanda"}))
            assert cards and cards[0]["document_id"]
            assert cards[0]["download_path"].endswith("/download")
    _run(go())


def test_it_mcp_03_read_document_returns_pages(client, auth, ids):
    doc_id_key = next(iter(ids["docs"]))
    doc = ids["docs"][doc_id_key]
    async def go():
        async with _mcp_session(_token(auth("abogada.alfa"))) as session:
            items = _items(await session.call_tool("read_document", {"case_id": ids["pago"],
                                                                     "document_id": doc["id"], "from_page": 1}))
            assert items and items[0]["source_type"] == "document_page" and items[0]["text"]
    _run(go())


def test_it_mcp_04_resources_files_and_graph_stats(client, auth, ids):
    async def go():
        async with _mcp_session(_token(auth("abogada.alfa"))) as session:
            files = await session.read_resource(f"case://{ids['pago']}/files")
            data = json.loads(files.contents[0].text)
            assert data["documents"], "la semilla debe tener documentos"
            stats = await session.read_resource(f"case://{ids['pago']}/graph/stats")
            sdata = json.loads(stats.contents[0].text)
            assert "nodes_by_type" in sdata and "edges_by_type" in sdata
    _run(go())


# ---------------------------------------------------------------------------
# Seguridad: sin token, otro tenant, y allowlist por rol (lectura vs corrección)
# ---------------------------------------------------------------------------

def test_it_mcp_05_no_token_is_401(client):
    async def go():
        from mcp_server.server import create_app
        app = create_app()
        async with LifespanManager(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://localhost") as h:
                r = await h.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                               "params": {"protocolVersion": "2025-03-26",
                                                          "capabilities": {},
                                                          "clientInfo": {"name": "t", "version": "0"}}})
                assert r.status_code == 401
                assert r.json()["error"]["code"] == "AUTH_REQUIRED"
    _run(go())


def test_it_mcp_06_cross_tenant_case_is_not_found(client, auth, ids):
    async def go():
        async with _mcp_session(_token(auth("abogada.alfa"))) as session:
            # 'lease' es un expediente de la org beta: 404 sin revelar existencia
            res = await session.call_tool("list_case_files", {"case_id": ids["lease"]})
            assert res.isError and "CASE_NOT_FOUND" in res.content[0].text
    _run(go())


def test_it_mcp_07_reviewer_role_cannot_correct_but_can_read(client, auth, ids):
    """Allowlist por rol: REVIEWER tiene ai.query (lee) pero no document.upload (no corrige)."""
    async def go():
        async with _mcp_session(_token(auth("revisor.alfa"))) as session:
            items = _items(await session.call_tool("list_case_files", {"case_id": ids["pago"]}))
            assert items
            res = await session.call_tool("correct_ocr_page", {
                "case_id": ids["pago"], "document_id": "11111111-1111-1111-1111-111111111111",
                "page_number": 1, "new_text": "x", "confirm": True})
            assert res.isError and "FORBIDDEN" in res.content[0].text
    _run(go())


def test_it_mcp_08_correction_requires_confirmation_preview(client, auth, ids, org_ids, owner_db):
    """Un usuario con permiso de escritura recibe VISTA PREVIA (no muta) sin confirm."""
    from helpers import fetch
    h = auth("abogada.alfa")
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    n = client.get(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages", headers=h).json()["pages"][0]["page_number"]
    before = fetch(owner_db, "SELECT text FROM document_pages WHERE document_id = %s AND page_number = %s",
                   (doc_id, n))[0][0]

    async def go():
        async with _mcp_session(_token(h)) as session:
            items = _items(await session.call_tool("correct_ocr_page", {
                "case_id": ids["pago"], "document_id": doc_id, "page_number": n,
                "new_text": "Texto cambiado vía MCP."}))
            assert items[0]["source_type"] == "correction_preview"
            assert items[0]["requires_confirmation"] is True
    _run(go())
    after = fetch(owner_db, "SELECT text FROM document_pages WHERE document_id = %s AND page_number = %s",
                  (doc_id, n))[0][0]
    assert after == before, "sin confirm la tool no puede mutar la página"
