"""IT-ISO — aislamiento del Chat IA por EXPEDIENTE (misma organización).

RLS aísla por organización, no por caso. Estas pruebas garantizan que ninguna
tool del chat (case_tools) ni la recuperación RAG devuelven datos de OTRO
proceso de la MISMA org. Patrón: la semilla `pago` tiene documentos, medios,
claims, evidencia y timeline; la semilla `vacio` (misma org) está vacía. Si una
tool olvidara el filtro `case_id`, al invocarla con `vacio` devolvería datos de
`pago` (toda la org) y la prueba lo detectaría por sus marcadores.
"""
from __future__ import annotations

import json

import pytest

from app.core.db import tx
from app.services import answering, case_tools
from app.services.case_tools import ToolContext

pytestmark = pytest.mark.integration


# Herramientas que operan sobre TODO el expediente y sus argumentos de sondeo.
CASE_WIDE_CALLS = [
    ("search_case", {"query": "pago transferencia contrato 80.000.000"}),
    ("search_documents", {"query": "demanda pago"}),
    ("list_speakers", {}),
    ("list_people_by_role", {"role": "juez"}),
    ("list_low_confidence_pages", {}),
    ("search_transcripts", {"query": "audiencia pago"}),
    ("search_transcript_by_time", {"query": "audiencia"}),
    ("locate", {"term": "80.000.000"}),
    ("get_file", {"query": "demanda"}),
    ("list_case_files", {}),
    ("search_claims", {"query": "pago"}),
    ("search_facts", {"query": "pago"}),
    ("search_evidence", {"query": "pago"}),
    ("search_timeline", {"query": "auto"}),
    ("get_timeline", {}),
    ("find_person", {"name": "PEREZ"}),
]


def _pago_markers(client, h, pago: str) -> set[str]:
    """Identificadores/textos únicos del expediente `pago`, para detectar fugas."""
    docs = client.get(f"/v1/cases/{pago}/documents", headers=h).json()
    media = client.get(f"/v1/cases/{pago}/media", headers=h).json()
    markers: set[str] = {"80.000.000", "Comercializadora Andina"}
    for d in docs:
        markers.add(d["id"])
        markers.add(d["filename"])
    for m in media:
        markers.add(m["id"])
        markers.add(m["filename"])
    return markers


def _leaks(payload, markers: set[str]) -> list[str]:
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    return sorted(m for m in markers if m in blob)


def test_it_iso_01_tools_no_escapan_a_otro_caso(client, auth, ids, org_ids):
    """Con case_id=`vacio`, NINGUNA tool puede devolver datos de `pago`."""
    h = auth("abogada.alfa")
    pago, vacio = ids["pago"], ids["vacio"]
    markers = _pago_markers(client, h, pago)

    pago_docs = client.get(f"/v1/cases/{pago}/documents", headers=h).json()
    pago_media = client.get(f"/v1/cases/{pago}/media", headers=h).json()
    pago_doc_id = pago_docs[0]["id"]

    ctx = ToolContext(org_id=org_ids["alfa"], actor_id=None)
    calls = list(CASE_WIDE_CALLS) + [
        ("read_document", {"document_id": pago_doc_id}),
        ("get_document_page", {"document_id": pago_doc_id, "page": 1}),
        ("get_document_markdown", {"document_id": pago_doc_id}),
        ("graph_query", {"start_node_id": pago_doc_id}),
        ("graph_neighbors", {"node_id": pago_doc_id}),
        ("event_relations", {"event": "EV-0001"}),
        ("process_path", {"event": "EV-0001"}),
    ]
    if pago_media:
        calls.append(("get_video_segment", {"media_id": pago_media[0]["id"]}))

    with tx(org_ids["alfa"], None) as conn:
        for tool, args in calls:
            try:
                out = case_tools.execute(conn, vacio, tool, args, ctx=ctx)
            except Exception:  # noqa: BLE001 — la tool puede rechazar un recurso ajeno: no es fuga
                continue
            leaked = _leaks(out, markers)
            assert not leaked, f"{tool} filtró datos de otro expediente: {leaked}"


def test_it_iso_02_control_positivo_las_tools_si_ven_su_caso(client, auth, ids, org_ids):
    """Sin esto, la prueba anterior pasaría trivialmente si las tools no devolvieran nada."""
    pago = ids["pago"]
    filename = next(iter(ids["docs"]))
    ctx = ToolContext(org_id=org_ids["alfa"], actor_id=None)
    with tx(org_ids["alfa"], None) as conn:
        files = case_tools.execute(conn, pago, "list_case_files", {}, ctx=ctx)
    blob = json.dumps(files, ensure_ascii=False, default=str)
    assert filename in blob, "list_case_files debería ver los documentos de su propio caso"


def test_it_iso_03_rag_no_recupera_de_otro_caso(client, auth, ids, org_ids):
    """La recuperación (FTS + pgvector) de `vacio` no debe traer chunks de `pago`."""
    h = auth("abogada.alfa")
    pago, vacio = ids["pago"], ids["vacio"]
    markers = _pago_markers(client, h, pago)
    with tx(org_ids["alfa"], None) as conn:
        items = answering.retrieve(conn, vacio, "pago de 80.000.000 contrato transferencia")
    assert not _leaks(items, markers), items


def test_it_iso_04_query_en_caso_vacio_no_cita_al_otro(client, auth, ids):
    """Preguntar por lo de `pago` estando en `vacio` no debe citar documentos de `pago`."""
    h = auth("abogada.alfa")
    pago, vacio = ids["pago"], ids["vacio"]
    pago_doc_ids = {d["id"] for d in client.get(f"/v1/cases/{pago}/documents", headers=h).json()}
    r = client.post(f"/v1/cases/{vacio}/query", headers=h,
                    json={"question": "¿Se pagó el $80.000.000 del contrato?", "strategy": "agent"})
    assert r.status_code == 200, r.text
    body = r.json()
    cited = {c.get("document_id") for c in (body.get("citations") or [])}
    assert not (cited & pago_doc_ids), f"citas de otro expediente: {cited & pago_doc_ids}"


def test_it_iso_05_sesion_de_chat_no_accesible_desde_otro_caso(client, auth, ids):
    """Una sesión creada en `pago` no es accesible a través de `vacio` (404)."""
    h = auth("abogada.alfa")
    pago, vacio = ids["pago"], ids["vacio"]
    sid = client.post(f"/v1/cases/{pago}/chats", headers=h, json={}).json()["id"]
    t = client.post(f"/v1/cases/{vacio}/chats/{sid}/messages", headers=h, json={"content": "hola"})
    assert t.status_code == 404, t.text
    g = client.get(f"/v1/cases/{vacio}/chats/{sid}/messages", headers=h)
    assert g.status_code == 404, g.text
