"""Prueba de extracción legal + grafo para un documento (una sola vez)."""
import sys
import time

sys.path.insert(0, "/srv/apps/api")

from app.core.db import tx
from app.services.legal_extraction import (extract_claims, extract_decisions, extract_entities,
                                            extract_events, link_evidence, detect_contradictions)
from app.services.graph import build_case_graph

ORG_ID = "b4e6d687-89b0-4b79-a0cc-69bd3532a7e9"
CASE_ID = "38865959-8d5f-44e4-bbc8-1226bd345b00"
USER_ID = "103c3eba-8c87-47cc-858e-1e3d299928c4"

doc_id = sys.argv[1]
t0 = time.time()
with tx(ORG_ID, USER_ID) as conn:
    ents = extract_entities(conn, ORG_ID, CASE_ID, "document", doc_id, USER_ID)
    cl = extract_claims(conn, ORG_ID, CASE_ID, "document", doc_id, USER_ID)
    ev = extract_events(conn, ORG_ID, CASE_ID, "document", doc_id, USER_ID)
    dec = extract_decisions(conn, ORG_ID, CASE_ID, "document", doc_id, USER_ID)
    le = link_evidence(conn, ORG_ID, CASE_ID, "document", doc_id, USER_ID)
    print(f"[{time.time()-t0:.0f}s] entidades={ents} claims={cl} eventos={ev} decisiones={dec}")
with tx(ORG_ID, USER_ID) as conn:
    g = build_case_graph(conn, ORG_ID, CASE_ID, USER_ID)
    print("grafo:", g)
