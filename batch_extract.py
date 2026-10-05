"""Extracción legal masiva sobre todos los documentos y media de un caso.

Puebla el grafo de conocimiento (entidades, claims, eventos, decisiones, hechos).
Reanudable: guarda los source_id ya procesados en un archivo de estado.
Uso: python batch_extract.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.core.db import rows, tx
from app.services.legal_extraction import (extract_claims, extract_decisions, extract_entities,
                                            extract_events, link_evidence)
from app.services.graph import build_case_graph

ORG_ID = "b4e6d687-89b0-4b79-a0cc-69bd3532a7e9"
CASE_ID = "38865959-8d5f-44e4-bbc8-1226bd345b00"
USER_ID = "103c3eba-8c87-47cc-858e-1e3d299928c4"
LOG = Path("/tmp/batch_extract.log")
STATE = Path("/tmp/batch_extract_state.json")


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def load_state() -> set[str]:
    if STATE.exists():
        return set(json.loads(STATE.read_text(encoding="utf-8")))
    return set()


def save_state(done: set[str]) -> None:
    STATE.write_text(json.dumps(sorted(done)), encoding="utf-8")


def main() -> None:
    done = load_state()
    with tx(ORG_ID, USER_ID) as conn:
        docs = rows(conn, "SELECT id, filename, page_count FROM documents WHERE case_id = :c ORDER BY page_count NULLS LAST", c=CASE_ID)
        media = rows(conn, "SELECT id, filename FROM media WHERE case_id = :c", c=CASE_ID)
    sources = [("document", str(d["id"]), f"{d['filename']} ({d['page_count']}p)") for d in docs]
    sources += [("media", str(m["id"]), m["filename"]) for m in media]
    log(f"=== extracción: {len(sources)} fuentes; ya hechas: {len(done)} ===")

    for i, (stype, sid, label) in enumerate(sources, 1):
        if sid in done:
            continue
        t0 = time.time()
        try:
            with tx(ORG_ID, USER_ID) as conn:
                res = {
                    "entities": extract_entities(conn, ORG_ID, CASE_ID, stype, sid, USER_ID),
                    "claims": extract_claims(conn, ORG_ID, CASE_ID, stype, sid, USER_ID),
                    "events": extract_events(conn, ORG_ID, CASE_ID, stype, sid, USER_ID),
                    "decisions": extract_decisions(conn, ORG_ID, CASE_ID, stype, sid, USER_ID),
                    "evidence": link_evidence(conn, ORG_ID, CASE_ID, stype, sid, USER_ID),
                }
            ents = res["entities"].get("entities", 0)
            cl = res["claims"].get("claims", 0)
            ev = res["events"].get("events", 0)
            dc = res["decisions"].get("decisions", 0)
            fa = res["decisions"].get("facts", 0)
            log(f"[{i}/{len(sources)}] {label} -> ent={ents} claims={cl} ev={ev} dec={dc} facts={fa} ({time.time()-t0:.0f}s)")
            done.add(sid)
            save_state(done)
        except Exception as exc:  # noqa: BLE001
            log(f"[{i}/{len(sources)}] {label} ERROR: {exc}")
            continue

    # Grafo final del caso
    try:
        with tx(ORG_ID, USER_ID) as conn:
            g = build_case_graph(conn, ORG_ID, CASE_ID, USER_ID)
        log(f"grafo final: {g}")
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR grafo: {exc}")
    log("=== FIN ===")


if __name__ == "__main__":
    main()
