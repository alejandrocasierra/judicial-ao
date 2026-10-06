#!/usr/bin/env python3
"""Extrae las PARTES de un expediente desde los encabezados de los autos y, con --confirm, las crea.

Uso:
    python scripts/extract_parties.py --case-id <uuid> --org-id <uuid>              # sólo lista candidatos
    python scripts/extract_parties.py --case-id <uuid> --org-id <uuid> --confirm    # las crea (deduplica)
    python scripts/extract_parties.py --case-id <uuid> --org-id <uuid> --confirm --min-mentions 2

En el VPS (tras importar el caso):
    docker compose --env-file .env.advisorlegal exec -T api \
      python /srv/scripts/extract_parties.py --case-id <uuid> --org-id <uuid> --confirm
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

from app.core.db import tx  # noqa: E402
from app.services import party_extraction  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Extrae y (opcional) crea las partes de un expediente")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--min-mentions", type=int, default=1)
    ap.add_argument("--confirm", action="store_true", help="crea las partes (si no, sólo lista)")
    a = ap.parse_args()

    with tx(a.org_id, a.user_id) as conn:
        cands = party_extraction.extract(conn, a.case_id)
        cands = [c for c in cands if c["mentions"] >= a.min_mentions]
        print(f"Candidatos: {len(cands)}")
        for c in cands:
            print(f"  [{c['role']:<13}] {c['name']:<42} {c['entity_type']:<12} {c['mentions']:>3} menciones"
                  f"  ({c['filename']} p.{c['page_number']})")
        if a.confirm and cands:
            created = party_extraction.create_parties(conn, a.org_id, a.case_id, a.user_id, cands)
            print(f"\n[OK] partes creadas: {len(created)}")
            for x in created:
                print(f"  + {x['name']} ({x['role']}, {x['entity_type']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
