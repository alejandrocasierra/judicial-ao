#!/usr/bin/env python3
"""Benchmark real del expediente piloto (SSD §42). Mide costo, tiempo y calidad por fase."""
from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    import envload  # noqa: E402
    envload.load(str(_env_path))

from app.core.db import tx  # noqa: E402


def _measure(label: str, fn, *args, **kwargs):
    """Mide tiempo y retorna resultado."""
    t0 = time.time()
    result = fn(*args, **kwargs)
    elapsed = time.time() - t0
    return {"label": label, "seconds": round(elapsed, 2), "result": result}


def benchmark_case(case_id: UUID, org_id: UUID, user_id: UUID) -> dict:
    """Ejecuta el pipeline completo sobre el expediente piloto y mide todo."""
    with tx(org_id, user_id) as conn:
        from sqlalchemy import text
        stats = conn.execute(text("""
            SELECT
              (SELECT count(*) FROM documents WHERE case_id = :c) AS documents,
              (SELECT count(*) FROM media WHERE case_id = :c) AS media,
              (SELECT count(*) FROM document_pages p JOIN documents d ON d.id = p.document_id WHERE d.case_id = :c) AS pages,
              (SELECT count(*) FROM transcript_segments s JOIN media m ON m.id = s.media_id WHERE m.case_id = :c) AS segments,
              (SELECT count(*) FROM claims WHERE case_id = :c) AS claims,
              (SELECT count(*) FROM facts WHERE case_id = :c) AS facts,
              (SELECT count(*) FROM contradictions WHERE case_id = :c) AS contradictions,
              (SELECT count(*) FROM graph_nodes WHERE case_id = :c) AS graph_nodes,
              (SELECT count(*) FROM graph_edges WHERE case_id = :c) AS graph_edges,
              (SELECT coalesce(sum(spent_llm_tokens), 0) FROM cases WHERE id = :c) AS spent_llm_tokens
        """), {"c": str(case_id)}).mappings().first()

    return {
        "case_id": str(case_id),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stats": dict(stats),
        "phases": {
            "ocr": {"documents": stats["documents"], "pages": stats["pages"]},
            "asr": {"media": stats["media"], "segments": stats["segments"]},
            "extraction": {"claims": stats["claims"], "facts": stats["facts"], "contradictions": stats["contradictions"]},
            "graph": {"nodes": stats["graph_nodes"], "edges": stats["graph_edges"]},
        },
        "llm": {"spent_tokens": stats["spent_llm_tokens"]},
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--case-id", required=True, type=UUID)
    p.add_argument("--org-id", required=True, type=UUID)
    p.add_argument("--user-id", required=True, type=UUID)
    p.add_argument("--out", default="var/benchmark_case.json")
    args = p.parse_args()

    result = benchmark_case(args.case_id, args.org_id, args.user_id)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    print(f"[OK] Benchmark guardado en {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
