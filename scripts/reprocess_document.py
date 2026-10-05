#!/usr/bin/env python3
"""Reprocesa el OCR de UN documento (y opcionalmente reindexa sus chunks).

Útil para volver a pasar el pipeline tras mejorar el motor OCR sin tocar los demás
documentos del caso. Respeta las páginas corregidas a mano (`human_corrected`).

Ejemplo:
    .venv\\Scripts\\python scripts\\reprocess_document.py \\
        --case-id 38865959-... --org-id b4e6d687-... --user-id 103c3eba-... \\
        --document-id d8426678-... --reindex
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.db import rows, tx  # noqa: E402
from app.services import indexing  # noqa: E402
from app.services.document_pipeline import process_document  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reprocess_document")


def _snapshot(org_id: str, user_id: str, document_id: str) -> dict[int, dict]:
    with tx(org_id, user_id) as conn:
        return {
            r["page_number"]: r
            for r in rows(conn, """SELECT page_number, ocr_confidence, needs_review, human_corrected, text
                                   FROM document_pages WHERE document_id = :d""", d=document_id)
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--document-id", required=True)
    ap.add_argument("--reindex", action="store_true", help="Reindexar chunks+embeddings tras el OCR")
    args = ap.parse_args()

    before = _snapshot(args.org_id, args.user_id, args.document_id)
    protected = sorted(pn for pn, r in before.items() if r["human_corrected"])
    log.info("Documento %s: %d páginas (%d corregidas a mano: %s)",
             args.document_id, len(before), len(protected), protected)

    with tx(args.org_id, args.user_id) as conn:
        conn.execute(text("UPDATE documents SET processing_status = 'OCR_PENDING' WHERE id = :d"),
                     {"d": args.document_id})

    with tx(args.org_id, args.user_id) as conn:
        result = process_document(conn, args.document_id, args.org_id, args.case_id, args.user_id)

    reindexed = None
    if args.reindex:
        with tx(args.org_id, args.user_id) as conn:
            reindexed = indexing.index_document(conn, args.org_id, args.case_id, args.document_id, args.user_id)

    after = _snapshot(args.org_id, args.user_id, args.document_id)
    changed = [pn for pn in after if pn in before and after[pn]["text"] != before[pn]["text"]]
    preserved = [pn for pn in protected if pn in after and after[pn]["text"] == before[pn]["text"]]
    delta = sum(float(after[p]["ocr_confidence"] or 0) - float(before[p]["ocr_confidence"] or 0)
                for p in after if p in before and p not in protected)

    report = {
        "document_id": args.document_id,
        "pipeline": result,
        "pages": len(after),
        "pages_changed": len(changed),
        "human_protected": protected,
        "human_preserved": preserved,
        "confidence_delta_sum": round(delta, 3),
        "reindexed": reindexed,
    }
    out = Path("var") / "reprocess_document_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("FIN: %s", json.dumps({k: v for k, v in report.items() if k != "pipeline"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
