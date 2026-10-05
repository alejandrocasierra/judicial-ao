"""UT-GRP — knowledge graph."""
import uuid

import pytest

from app.services import graph as gr

pytestmark = pytest.mark.unit

ORG = str(uuid.uuid4())
CASE = str(uuid.uuid4())
DOC = str(uuid.uuid4())
CLAIM = str(uuid.uuid4())
FACT = str(uuid.uuid4())


def test_ut_grp_01_table_for_target():
    assert gr._table_for_target("claim") == "claims"
    assert gr._table_for_target("entity") == "entities"


def test_ut_grp_02_build_inserts_nodes_and_edges(monkeypatch):
    calls: dict[str, list] = {"nodes": [], "edges": []}

    def fake_delete(_conn, case_id):
        calls["deleted"] = case_id

    def fake_insert_node(_conn, org_id, case_id, node_type, source_table, source_id, label, metadata=None):
        nid = str(uuid.uuid4())
        calls["nodes"].append({"type": node_type, "source_table": source_table, "source_id": source_id, "label": label})
        return nid

    def fake_insert_edge(_conn, org_id, case_id, src, tgt, edge_type, provenance, confidence=None, metadata=None):
        calls["edges"].append({"src": src, "tgt": tgt, "type": edge_type, "provenance": provenance})

    def fake_node_id(_conn, case_id, source_table, source_id):
        return str(uuid.uuid4())

    monkeypatch.setattr(gr, "_delete_case_graph", fake_delete)
    monkeypatch.setattr(gr, "_insert_node", fake_insert_node)
    monkeypatch.setattr(gr, "_insert_edge", fake_insert_edge)
    monkeypatch.setattr(gr, "_node_id", fake_node_id)

    def _rows(_c, sql: str, **params):
        if "count(*)" in sql:
            return [{"n": 1}]
        if "FROM cases" in sql:
            return [{"case_number": "2018-361", "title": "Caso prueba"}]
        if "FROM documents" in sql or "FROM media" in sql:
            return [{"id": uuid.uuid4(), "filename": "doc.pdf", "document_type": "demand", "description": "x"}]
        if "FROM entities" in sql:
            return [{"id": uuid.uuid4(), "entity_type": "person", "name": "Juan"}]
        return []

    monkeypatch.setattr(gr, "rows", _rows)

    result = gr.build_case_graph(object(), ORG, CASE)
    assert result["nodes"] >= 1
    assert calls["nodes"][0]["type"] == "Case"


def test_ut_grp_03_evidence_matrix(monkeypatch):
    fact_id = str(uuid.uuid4())
    ev_id = str(uuid.uuid4())

    def _rows(_c, sql: str, **params):
        if "node_type = 'Fact'" in sql:
            return [{"id": fact_id, "label": "Hecho A", "fact_id": str(uuid.uuid4())}]
        if "node_type = 'Evidence'" in sql:
            return [{"id": ev_id, "label": "Prueba 1", "evidence_id": str(uuid.uuid4())}]
        if "edge_type IN" in sql:
            return [{"source_node_id": ev_id, "target_node_id": fact_id, "edge_type": "SUPPORTS"}]
        return []

    monkeypatch.setattr(gr, "rows", _rows)
    result = gr.evidence_matrix(object(), CASE)
    assert len(result["facts"]) == 1
    assert result["relations"][fact_id][ev_id] == "SUPPORTS"


def test_ut_grp_04_find_node(monkeypatch):
    node_id = str(uuid.uuid4())

    def _rows(_c, sql: str, **params):
        assert params.get("nt") == "Document"
        assert params.get("q") == "%demanda%"
        return [{"id": node_id, "label": "demanda.pdf", "node_type": "Document"}]

    monkeypatch.setattr(gr, "rows", _rows)
    items = gr.find_node(object(), CASE, node_type="Document", query="demanda")
    assert items[0]["id"] == node_id


def test_ut_grp_05_speaker_nodes_use_display_name(monkeypatch):
    """Los hablantes son nodos del grafo etiquetados con su nombre editado."""
    calls: dict[str, list] = {"nodes": [], "edges": []}
    spk = str(uuid.uuid4())
    media_id = str(uuid.uuid4())

    def fake_insert_node(_c, _o, _cid, node_type, source_table, source_id, label, metadata=None):
        calls["nodes"].append({"type": node_type, "source_table": source_table, "label": label})
        return str(uuid.uuid4())

    def fake_insert_edge(_c, _o, _cid, src, tgt, edge_type, provenance, confidence=None, metadata=None):
        calls["edges"].append({"type": edge_type})

    monkeypatch.setattr(gr, "_delete_case_graph", lambda *a: None)
    monkeypatch.setattr(gr, "_insert_node", fake_insert_node)
    monkeypatch.setattr(gr, "_insert_edge", fake_insert_edge)
    monkeypatch.setattr(gr, "_node_id", lambda *a: str(uuid.uuid4()))

    def _rows(_c, sql: str, **params):
        if "count(*)" in sql:
            return [{"n": 1}]
        if "FROM cases" in sql:
            return [{"case_number": "2018-361", "title": "Caso"}]
        if "FROM speakers" in sql:
            return [{"id": spk, "label": "SPK-03", "display_name": "Álvaro Lúzico Álvarez",
                     "speaker_role": "judge", "resolution_status": "PROBABLE", "resolved_party_id": None}]
        if "FROM transcript_segments" in sql:
            return [{"media_id": media_id, "speaker_id": spk}]
        if "FROM media" in sql:
            return [{"id": media_id, "filename": "audiencia.mp4"}]
        return []

    monkeypatch.setattr(gr, "rows", _rows)
    gr.build_case_graph(object(), ORG, CASE)
    speaker_nodes = [n for n in calls["nodes"] if n["type"] == "Speaker"]
    assert speaker_nodes and speaker_nodes[0]["label"] == "Álvaro Lúzico Álvarez"
    assert any(e["type"] == "HAS_SPEAKER" for e in calls["edges"])
