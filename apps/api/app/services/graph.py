"""Knowledge Graph sobre PostgreSQL (SSD §16).

No es una red neuronal: es una proyección de las tablas existentes
(entities, claims, facts, events, decisions, evidence, citations, etc.)
a nodos y aristas con procedencia EXTRACTED/INFERRED/AMBIGUOUS.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import rows

log = logging.getLogger(__name__)

NODE_TYPES = {
    "cases": "Case",
    "documents": "Document",
    "media": "Document",  # se trata como nodo Document en el grafo
    "entities": None,     # depende de entity_type
    "parties": None,      # depende de entity_type
    "claims": "Claim",
    "facts": "Fact",
    "events": "Event",
    "decisions": "Decision",
    "evidence": "Evidence",
    "issues": "Issue",
    "speakers": "Speaker",
}


def _delete_case_graph(conn: Connection, case_id: str) -> None:
    conn.execute(text("DELETE FROM graph_edges WHERE case_id = :c"), {"c": case_id})
    conn.execute(text("DELETE FROM graph_nodes WHERE case_id = :c"), {"c": case_id})


def _node_id(conn: Connection, case_id: str, source_table: str, source_id: str | None) -> str | None:
    if not source_id:
        return None
    r = conn.execute(
        text("SELECT id FROM graph_nodes WHERE case_id = :c AND source_table = :t AND source_id = :s"),
        {"c": case_id, "t": source_table, "s": source_id},
    ).scalar()
    return str(r) if r else None


def _insert_node(conn: Connection, org_id: str, case_id: str, node_type: str, source_table: str | None,
                 source_id: str | None, label: str, metadata: dict[str, Any] | None = None) -> str:
    import json
    r = conn.execute(
        text("""
            INSERT INTO graph_nodes (organization_id, case_id, node_type, source_table, source_id, label, metadata)
            VALUES (:o, :c, :nt, :st, :sid, :label, CAST(:meta AS jsonb))
            ON CONFLICT (case_id, source_table, source_id, node_type) DO UPDATE SET label = EXCLUDED.label, metadata = EXCLUDED.metadata
            RETURNING id
        """),
        {"o": org_id, "c": case_id, "nt": node_type, "st": source_table, "sid": source_id,
         "label": label, "meta": json.dumps(metadata or {}, ensure_ascii=False)},
    )
    return str(r.scalar_one())


def _insert_edge(conn: Connection, org_id: str, case_id: str, source_node_id: str, target_node_id: str,
                 edge_type: str, provenance: str, confidence: float | None = None,
                 metadata: dict[str, Any] | None = None) -> None:
    if source_node_id == target_node_id:
        return
    import json
    conn.execute(
        text("""
            INSERT INTO graph_edges (organization_id, case_id, source_node_id, target_node_id,
                                     edge_type, provenance, confidence, metadata)
            VALUES (:o, :c, :sn, :tn, :et, :prov, :conf, CAST(:meta AS jsonb))
            ON CONFLICT DO NOTHING
        """),
        {"o": org_id, "c": case_id, "sn": source_node_id, "tn": target_node_id,
         "et": edge_type, "prov": provenance, "conf": confidence, "meta": json.dumps(metadata or {}, ensure_ascii=False)},
    )


def build_case_graph(conn: Connection, org_id: str, case_id: str, actor_id: str | None = None) -> dict[str, int]:
    """Reconstruye el grafo del caso desde las tablas de conocimiento."""
    _delete_case_graph(conn, case_id)

    # Nodo Case
    case_row = rows(conn, "SELECT case_number, title FROM cases WHERE id = :c", c=case_id)[0]
    case_node = _insert_node(conn, org_id, case_id, "Case", "cases", case_id,
                             case_row["case_number"] or case_row["title"] or case_id)

    # Documentos y media
    docs = rows(conn, "SELECT id, filename, document_type FROM documents WHERE case_id = :c", c=case_id)
    for d in docs:
        _insert_node(conn, org_id, case_id, "Document", "documents", str(d["id"]), d["filename"],
                     {"document_type": d["document_type"]})
    media = rows(conn, "SELECT id, filename FROM media WHERE case_id = :c", c=case_id)
    for m in media:
        _insert_node(conn, org_id, case_id, "Document", "media", str(m["id"]), m["filename"], {"kind": "media"})

    # Entidades (solo Person y Organization como nodos; el resto se vincula mediante atributos)
    entities = rows(conn, "SELECT id, entity_type, name FROM entities WHERE case_id = :c", c=case_id)
    for e in entities:
        if e["entity_type"] not in {"person", "organization"}:
            continue
        nt = e["entity_type"].title()
        _insert_node(conn, org_id, case_id, nt, "entities", str(e["id"]), e["name"],
                     {"entity_type": e["entity_type"]})

    # Partes (como Organization)
    parties = rows(conn, "SELECT id, name, role, entity_type FROM parties WHERE case_id = :c", c=case_id)
    for p in parties:
        node_type = "Person" if p["entity_type"] == "person" else "Organization"
        _insert_node(conn, org_id, case_id, node_type, "parties", str(p["id"]), p["name"],
                     {"role": p["role"]})

    # Hablantes (diarización) como nodos propios: la etiqueta usa el nombre editado
    # (`display_name`) para que renombrar un hablante se refleje al reconstruir el grafo.
    speakers = rows(conn, """SELECT id, label, display_name, speaker_role, resolution_status, resolved_party_id
                             FROM speakers WHERE case_id = :c""", c=case_id)
    for sp in speakers:
        _insert_node(conn, org_id, case_id, "Speaker", "speakers", str(sp["id"]),
                     sp["display_name"] or sp["label"],
                     {"label": sp["label"], "speaker_role": sp["speaker_role"],
                      "resolution_status": sp["resolution_status"]})

    # Claims, facts, events, decisions, evidence, issues, legal_rules
    claims = rows(conn, "SELECT id, text FROM claims WHERE case_id = :c", c=case_id)
    for x in claims:
        _insert_node(conn, org_id, case_id, "Claim", "claims", str(x["id"]), x["text"][:200])
    facts = rows(conn, "SELECT id, proposition FROM facts WHERE case_id = :c", c=case_id)
    for x in facts:
        _insert_node(conn, org_id, case_id, "Fact", "facts", str(x["id"]), x["proposition"][:200])
    events = rows(conn, "SELECT id, description FROM events WHERE case_id = :c", c=case_id)
    for x in events:
        _insert_node(conn, org_id, case_id, "Event", "events", str(x["id"]), x["description"][:200])
    decisions = rows(conn, "SELECT id, outcome FROM decisions WHERE case_id = :c", c=case_id)
    for x in decisions:
        _insert_node(conn, org_id, case_id, "Decision", "decisions", str(x["id"]), x["outcome"] or "Decisión")
    evidence = rows(conn, "SELECT id, description, evidence_type FROM evidence WHERE case_id = :c", c=case_id)
    for x in evidence:
        _insert_node(conn, org_id, case_id, "Evidence", "evidence", str(x["id"]), x["description"][:200],
                     {"evidence_type": x["evidence_type"]})
    issues = rows(conn, "SELECT id, description FROM issues WHERE case_id = :c", c=case_id)
    for x in issues:
        _insert_node(conn, org_id, case_id, "Issue", "issues", str(x["id"]), x["description"][:200])

    # Aristas: citaciones
    citations = rows(conn, """
        SELECT target_type, target_id, source_type, document_id, media_id, page_number, segment_id
        FROM citations WHERE case_id = :c
    """, c=case_id)
    for cit in citations:
        src_table = "documents" if cit["source_type"] == "document_page" else "media"
        src_id = str(cit["document_id"] or cit["media_id"])
        src_node = _node_id(conn, case_id, src_table, src_id)
        tgt_node = _node_id(conn, case_id, _table_for_target(cit["target_type"]), str(cit["target_id"]))
        if src_node and tgt_node:
            meta = {"page_number": cit["page_number"], "segment_id": str(cit["segment_id"]) if cit["segment_id"] else None}
            _insert_edge(conn, org_id, case_id, src_node, tgt_node, "CITES", "EXTRACTED", metadata=meta)

    # Aristas: fact_claims (Claim ASSERTS/REFUTES Fact)
    fcs = rows(conn, """
        SELECT fc.claim_id, fc.fact_id, fc.stance
        FROM fact_claims fc
        JOIN facts f ON f.id = fc.fact_id
        WHERE f.case_id = :c
    """, c=case_id)
    for fc in fcs:
        src = _node_id(conn, case_id, "claims", str(fc["claim_id"]))
        tgt = _node_id(conn, case_id, "facts", str(fc["fact_id"]))
        if src and tgt:
            et = "ASSERTS" if fc["stance"] == "asserts" else "REFUTES"
            _insert_edge(conn, org_id, case_id, src, tgt, et, "EXTRACTED")

    # Aristas: evidence_links (Evidence SUPPORTS/REFUTES/ABOUT Fact)
    els = rows(conn, """
        SELECT el.evidence_id, el.fact_id, el.stance
        FROM evidence_links el
        JOIN evidence ev ON ev.id = el.evidence_id
        WHERE ev.case_id = :c
    """, c=case_id)
    for el in els:
        src = _node_id(conn, case_id, "evidence", str(el["evidence_id"]))
        tgt = _node_id(conn, case_id, "facts", str(el["fact_id"]))
        if src and tgt:
            et = {"supports": "SUPPORTS", "refutes": "REFUTES", "neutral": "ABOUT"}.get(el["stance"], "ABOUT")
            _insert_edge(conn, org_id, case_id, src, tgt, et, "EXTRACTED")

    # Aristas: contradicciones (Claim CONTRADICTS Claim)
    contras = rows(conn, "SELECT claim_a_id, claim_b_id FROM contradictions WHERE case_id = :c", c=case_id)
    for co in contras:
        a = _node_id(conn, case_id, "claims", str(co["claim_a_id"]))
        b = _node_id(conn, case_id, "claims", str(co["claim_b_id"]))
        if a and b:
            _insert_edge(conn, org_id, case_id, a, b, "CONTRADICTS", "EXTRACTED", confidence=1.0)
            _insert_edge(conn, org_id, case_id, b, a, "CONTRADICTS", "EXTRACTED", confidence=1.0)

    # Aristas: decisiones determinan facts
    det_facts = rows(conn, "SELECT id, determined_by_decision_id FROM facts WHERE case_id = :c AND determined_by_decision_id IS NOT NULL", c=case_id)
    for f in det_facts:
        src = _node_id(conn, case_id, "decisions", str(f["determined_by_decision_id"]))
        tgt = _node_id(conn, case_id, "facts", str(f["id"]))
        if src and tgt:
            _insert_edge(conn, org_id, case_id, src, tgt, "DECIDES", "EXTRACTED", confidence=1.0)

    # Aristas: participantes (entities) PARTICIPATED_IN eventos
    evts = rows(conn, "SELECT id, participants FROM events WHERE case_id = :c", c=case_id)
    for e in evts:
        for pid in (e["participants"] or []):
            src = _node_id(conn, case_id, "entities", str(pid))
            tgt = _node_id(conn, case_id, "events", str(e["id"]))
            if src and tgt:
                _insert_edge(conn, org_id, case_id, src, tgt, "PARTICIPATED_IN", "EXTRACTED")

    # Aristas: claimant_party ASSERTS claim
    claim_parties = rows(conn, "SELECT id, claimant_party_id FROM claims WHERE case_id = :c AND claimant_party_id IS NOT NULL", c=case_id)
    for cp in claim_parties:
        src = _node_id(conn, case_id, "parties", str(cp["claimant_party_id"]))
        tgt = _node_id(conn, case_id, "claims", str(cp["id"]))
        if src and tgt:
            _insert_edge(conn, org_id, case_id, src, tgt, "ASSERTS", "EXTRACTED")

    # Aristas: evidence -> source document/media
    ev_src = rows(conn, "SELECT id, source_document_id, source_media_id FROM evidence WHERE case_id = :c", c=case_id)
    for ev in ev_src:
        src = _node_id(conn, case_id, "evidence", str(ev["id"]))
        if ev["source_document_id"]:
            tgt = _node_id(conn, case_id, "documents", str(ev["source_document_id"]))
        else:
            tgt = _node_id(conn, case_id, "media", str(ev["source_media_id"]))
        if src and tgt:
            _insert_edge(conn, org_id, case_id, src, tgt, "CITES", "EXTRACTED")

    # Aristas: decision -> source document
    dec_src = rows(conn, "SELECT id, source_document_id FROM decisions WHERE case_id = :c", c=case_id)
    for d in dec_src:
        src = _node_id(conn, case_id, "decisions", str(d["id"]))
        tgt = _node_id(conn, case_id, "documents", str(d["source_document_id"]))
        if src and tgt:
            _insert_edge(conn, org_id, case_id, src, tgt, "CITES", "EXTRACTED")

    # Aristas: caso -> documentos/media (ABOUT)
    for d in docs:
        dn = _node_id(conn, case_id, "documents", str(d["id"]))
        if dn:
            _insert_edge(conn, org_id, case_id, case_node, dn, "ABOUT", "EXTRACTED")
    for m in media:
        mn = _node_id(conn, case_id, "media", str(m["id"]))
        if mn:
            _insert_edge(conn, org_id, case_id, case_node, mn, "ABOUT", "EXTRACTED")

    # Aristas: media en la que intervino cada hablante (Document HAS_SPEAKER Speaker)
    media_speakers = rows(conn, """
        SELECT DISTINCT s.media_id, s.speaker_id
        FROM transcript_segments s JOIN media m ON m.id = s.media_id
        WHERE m.case_id = :c AND s.speaker_id IS NOT NULL
    """, c=case_id)
    for ms in media_speakers:
        src = _node_id(conn, case_id, "media", str(ms["media_id"]))
        tgt = _node_id(conn, case_id, "speakers", str(ms["speaker_id"]))
        if src and tgt:
            _insert_edge(conn, org_id, case_id, src, tgt, "HAS_SPEAKER", "EXTRACTED")

    # Aristas: hablante resuelto a una parte (Speaker IS_PARTY Party)
    for sp in speakers:
        if sp["resolved_party_id"]:
            src = _node_id(conn, case_id, "speakers", str(sp["id"]))
            tgt = _node_id(conn, case_id, "parties", str(sp["resolved_party_id"]))
            if src and tgt:
                _insert_edge(conn, org_id, case_id, src, tgt, "IS_PARTY", "EXTRACTED")

    node_count = rows(conn, "SELECT count(*) AS n FROM graph_nodes WHERE case_id = :c", c=case_id)[0]["n"]
    edge_count = rows(conn, "SELECT count(*) AS n FROM graph_edges WHERE case_id = :c", c=case_id)[0]["n"]
    log.info("graph built case=%s nodes=%s edges=%s", case_id, node_count, edge_count)
    return {"nodes": node_count, "edges": edge_count}


def _table_for_target(target_type: str) -> str:
    return {
        "entity": "entities",
        "claim": "claims",
        "fact": "facts",
        "event": "events",
        "decision": "decisions",
        "evidence": "evidence",
        "answer": "claims",  # respuestas se vinculan a claims por similitud de cita
    }.get(target_type, "entities")


def traverse(conn: Connection, case_id: str, start_node_id: str, depth: int = 2,
             edge_types: list[str] | None = None) -> dict[str, Any]:
    """Recorre el grafo desde un nodo usando CTE recursiva."""
    type_filter = ""
    params: dict[str, Any] = {"c": case_id, "start": start_node_id, "depth": depth}
    if edge_types:
        type_filter = "AND e.edge_type = ANY(CAST(:types AS text[]))"
        params["types"] = "{" + ",".join(edge_types) + "}"
    base = """
        WITH RECURSIVE walk(node_id, path, d) AS (
          SELECT CAST(:start AS uuid), ARRAY[CAST(:start AS text)], 0
          UNION ALL
          SELECT CAST(CASE WHEN e.source_node_id = w.node_id THEN e.target_node_id ELSE e.source_node_id END AS uuid),
                 w.path || CAST(CASE WHEN e.source_node_id = w.node_id
                                THEN e.target_node_id ELSE e.source_node_id END AS text),
                 w.d + 1
          FROM walk w
          JOIN graph_edges e ON (e.source_node_id = w.node_id OR e.target_node_id = w.node_id)
            AND e.case_id = :c
            TYPE_FILTER_PLACEHOLDER
          WHERE w.d < :depth
            AND CAST(CASE WHEN e.source_node_id = w.node_id
                     THEN e.target_node_id ELSE e.source_node_id END AS text)
                <> ALL(w.path)
        )
    """
    nodes_sql = base + "SELECT DISTINCT n.* FROM walk w JOIN graph_nodes n ON n.id = w.node_id"
    edges_sql = base + """SELECT DISTINCT e.* FROM walk w
        JOIN graph_edges e ON e.source_node_id = w.node_id OR e.target_node_id = w.node_id
        WHERE e.case_id = :c"""
    nodes_sql = nodes_sql.replace("TYPE_FILTER_PLACEHOLDER", type_filter)
    edges_sql = edges_sql.replace("TYPE_FILTER_PLACEHOLDER", type_filter)
    nodes = rows(conn, nodes_sql, **params)
    edges = rows(conn, edges_sql, **params)
    return {"start_node_id": start_node_id, "depth": depth, "nodes": nodes, "edges": edges}


def evidence_matrix(conn: Connection, case_id: str) -> dict[str, Any]:
    """Matriz de evidencia: facts (filas) vs evidence (columnas) con relación."""
    facts = rows(conn, """
        SELECT n.id, n.label, n.source_id AS fact_id
        FROM graph_nodes n
        WHERE n.case_id = :c AND n.node_type = 'Fact' ORDER BY n.label
    """, c=case_id)
    evidence = rows(conn, """
        SELECT n.id, n.label, n.source_id AS evidence_id
        FROM graph_nodes n
        WHERE n.case_id = :c AND n.node_type = 'Evidence' ORDER BY n.label
    """, c=case_id)
    edges = rows(conn, """
        SELECT e.source_node_id, e.target_node_id, e.edge_type
        FROM graph_edges e
        WHERE e.case_id = :c AND e.edge_type IN ('SUPPORTS','REFUTES','ABOUT')
    """, c=case_id)
    relation: dict[str, dict[str, str]] = {str(f["id"]): {} for f in facts}
    for e in edges:
        for f in facts:
            if e["target_node_id"] == f["id"] and any(ev["id"] == e["source_node_id"] for ev in evidence):
                relation[str(f["id"])][str(e["source_node_id"])] = e["edge_type"]
    return {"facts": facts, "evidence": evidence, "relations": relation}


def find_node(conn: Connection, case_id: str, node_type: str | None = None,
              query: str | None = None, limit: int = 20) -> list[dict]:
    sql = "SELECT * FROM graph_nodes WHERE case_id = :c"
    params: dict[str, Any] = {"c": case_id, "limit": limit}
    if node_type:
        sql += " AND node_type = :nt"
        params["nt"] = node_type
    if query:
        sql += " AND label ILIKE :q"
        params["q"] = f"%{query}%"
    sql += " ORDER BY label LIMIT :limit"
    return rows(conn, sql, **params)
