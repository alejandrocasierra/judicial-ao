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


def test_ut_grp_06_event_relationships_projected_to_graph(monkeypatch):
    """build_case_graph proyecta event_relationships a graph_edges con tipos y provenance correctos."""
    calls: dict[str, list] = {"nodes": [], "edges": []}
    node_map: dict[tuple[str, str], str] = {}

    def fake_insert_node(_c, _o, _cid, node_type, source_table, source_id, label, metadata=None):
        nid = str(uuid.uuid4())
        node_map[(source_table, source_id)] = nid
        calls["nodes"].append({"type": node_type, "source_table": source_table, "source_id": source_id, "label": label})
        return nid

    def fake_insert_edge(_c, _o, _cid, src, tgt, edge_type, provenance, confidence=None, metadata=None):
        calls["edges"].append({
            "src": src, "tgt": tgt, "type": edge_type,
            "provenance": provenance, "confidence": confidence, "metadata": metadata,
        })

    def fake_node_id(_c, _cid, source_table, source_id):
        return node_map.get((source_table, source_id))

    monkeypatch.setattr(gr, "_delete_case_graph", lambda *a: None)
    monkeypatch.setattr(gr, "_insert_node", fake_insert_node)
    monkeypatch.setattr(gr, "_insert_edge", fake_insert_edge)
    monkeypatch.setattr(gr, "_node_id", fake_node_id)

    e1 = str(uuid.uuid4())
    e2 = str(uuid.uuid4())
    e3 = str(uuid.uuid4())

    def _rows(_c, sql: str, **params):
        if "count(*)" in sql:
            return [{"n": 1}]
        if "FROM cases" in sql:
            return [{"case_number": "2018-361", "title": "Caso prueba"}]
        if "FROM events" in sql and "event_relationships" not in sql:
            return [
                {"id": uuid.UUID(e1), "description": "Demanda", "participants": []},
                {"id": uuid.UUID(e2), "description": "Auto admisorio", "participants": []},
                {"id": uuid.UUID(e3), "description": "Sentencia", "participants": []},
            ]
        if "FROM event_relationships" in sql:
            return [
                {"source_event_id": uuid.UUID(e1), "target_event_id": uuid.UUID(e2),
                 "relationship": "precede", "confidence": 0.7, "provenance": "INFERRED"},
                {"source_event_id": uuid.UUID(e2), "target_event_id": uuid.UUID(e3),
                 "relationship": "causes", "confidence": 0.85, "provenance": "EXTRACTED"},
                {"source_event_id": uuid.UUID(e3), "target_event_id": uuid.UUID(e1),
                 "relationship": "appeals", "confidence": 0.6, "provenance": "INFERRED"},
                {"source_event_id": uuid.UUID(e1), "target_event_id": uuid.UUID(e3),
                 "relationship": "responds_to", "confidence": 0.75, "provenance": "EXTRACTED"},
                {"source_event_id": uuid.UUID(e2), "target_event_id": uuid.UUID(e1),
                 "relationship": "refers_to", "confidence": 0.5, "provenance": "INFERRED"},
            ]
        return []

    monkeypatch.setattr(gr, "rows", _rows)
    gr.build_case_graph(object(), ORG, CASE)

    edges_by_type = {e["type"]: e for e in calls["edges"]}
    assert "PRECEDES" in edges_by_type
    assert edges_by_type["PRECEDES"]["provenance"] == "INFERRED"
    assert edges_by_type["PRECEDES"]["confidence"] == pytest.approx(0.7)
    assert edges_by_type["PRECEDES"]["metadata"] == {"relationship_type": "precede"}
    assert "CAUSES" in edges_by_type
    assert edges_by_type["CAUSES"]["provenance"] == "EXTRACTED"
    assert edges_by_type["CAUSES"]["confidence"] == pytest.approx(0.85)
    assert "APPEALS" in edges_by_type
    assert "RESPONDS_TO" in edges_by_type
    assert "REFERS_TO" in edges_by_type


def test_ut_grp_07_event_relationships_skip_orphan_events(monkeypatch):
    """No se crean aristas si uno de los eventos no tiene nodo en el grafo."""
    calls: dict[str, list] = {"edges": []}
    existing_nodes: dict[tuple[str, str], str] = {}

    def fake_insert_node(_c, _o, _cid, node_type, source_table, source_id, label, metadata=None):
        nid = str(uuid.uuid4())
        existing_nodes[(source_table, source_id)] = nid
        return nid

    def fake_insert_edge(_c, _o, _cid, src, tgt, edge_type, provenance, confidence=None, metadata=None):
        calls["edges"].append({"src": src, "tgt": tgt, "type": edge_type})

    def fake_node_id(_c, _cid, source_table, source_id):
        return existing_nodes.get((source_table, source_id))

    monkeypatch.setattr(gr, "_delete_case_graph", lambda *a: None)
    monkeypatch.setattr(gr, "_insert_node", fake_insert_node)
    monkeypatch.setattr(gr, "_insert_edge", fake_insert_edge)
    monkeypatch.setattr(gr, "_node_id", fake_node_id)

    e1 = str(uuid.uuid4())

    def _rows(_c, sql: str, **params):
        if "count(*)" in sql:
            return [{"n": 1}]
        if "FROM cases" in sql:
            return [{"case_number": "2018-361", "title": "Caso prueba"}]
        if "FROM events" in sql and "event_relationships" not in sql:
            return [{"id": uuid.UUID(e1), "description": "Evento real", "participants": []}]
        if "FROM event_relationships" in sql:
            return [
                {"source_event_id": uuid.UUID(e1), "target_event_id": uuid.uuid4(),
                 "relationship": "precede", "confidence": 0.7, "provenance": "INFERRED"},
            ]
        return []

    monkeypatch.setattr(gr, "rows", _rows)
    gr.build_case_graph(object(), ORG, CASE)
    assert not any(e["type"] == "PRECEDES" for e in calls["edges"])
