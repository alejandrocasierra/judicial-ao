#!/usr/bin/env python3
"""Extrae ACTUACIONES PROCESALES (línea de tiempo procesal) de los documentos de un expediente.

Uso:
    python scripts/extract_timeline.py --case-id <uuid> --org-id <uuid> [--user-id <uuid>] \
        [--limit N] [--only-missing]

En el VPS:
    docker compose --env-file .env.advisorlegal exec -T api \
      python /srv/scripts/extract_timeline.py --case-id <uuid> --org-id <uuid> --only-missing
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

_env = Path(os.environ["ENV_FILE"]) if os.environ.get("ENV_FILE") else ROOT / ".env"
if not _env.is_absolute():
    _env = ROOT / _env
if _env.exists():
    import envload  # noqa: E402

    envload.load(str(_env), override=True)

from app.core.db import rows, tx  # noqa: E402
from app.services import legal_extraction  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Extrae actuaciones procesales (línea de tiempo) de un expediente")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--limit", type=int, default=0, help="Máximo de documentos a procesar (0 = todos)")
    ap.add_argument("--only-missing", action="store_true", help="Solo documentos sin actuaciones extraídas")
    a = ap.parse_args()

    if not a.user_id:
        with tx(a.org_id, None) as c:
            u = rows(c, "SELECT id FROM users WHERE organization_id = :o ORDER BY created_at LIMIT 1", o=a.org_id)
        a.user_id = str(u[0]["id"]) if u else None
    if not a.user_id:
        print("ERROR: la organización no tiene usuarios; pasa --user-id <uuid>")
        return 2
    actor = a.user_id

    with tx(a.org_id, a.user_id) as c:
        docs = [dict(r) for r in rows(
            c, "SELECT id, filename FROM documents WHERE case_id = :c ORDER BY filename", c=a.case_id)]
    if a.only_missing:
        with tx(a.org_id, a.user_id) as c:
            have = {str(r["document_id"]) for r in rows(
                c, """SELECT DISTINCT document_id FROM events
                      WHERE case_id = :c AND kind = 'procedural' AND document_id IS NOT NULL""", c=a.case_id)}
        docs = [d for d in docs if str(d["id"]) not in have]
    if a.limit:
        docs = docs[:a.limit]

    total = 0
    for d in docs:
        try:
            with tx(a.org_id, a.user_id) as c:
                r = legal_extraction.extract_procedural_events(
                    c, a.org_id, a.case_id, "document", str(d["id"]), actor)
            n = int(r.get("events", 0))
            total += n
            print(f"  {str(d['filename'])[:64]:<64} -> {n} actuaciones")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {str(d['filename'])[:50]}: {exc}")
    print(f"\n[OK] documentos={len(docs)} actuaciones={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
