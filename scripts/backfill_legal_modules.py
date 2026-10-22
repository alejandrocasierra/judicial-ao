"""Backfill idempotente del ledger de hechos.

Uso:
  python scripts/backfill_legal_modules.py --case-id <uuid>
  python scripts/backfill_legal_modules.py --all
  python scripts/backfill_legal_modules.py --all --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))
import envload  # noqa: E402
envload.load(str(_ROOT / ".env"))

from sqlalchemy import create_engine, text  # noqa: E402

from app.core.db import tx  # noqa: E402
from app.services import party_facts  # noqa: E402


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variable de entorno requerida: {name}")
    return value


def _owner_engine():
    url = (f"postgresql+psycopg://{_env('POSTGRES_SUPERUSER')}:{quote_plus(_env('POSTGRES_SUPERUSER_PASSWORD'))}"
           f"@{_env('POSTGRES_HOST')}:{_env('POSTGRES_PORT')}/{_env('POSTGRES_DB')}")
    return create_engine(url)


def _case_rows():
    with _owner_engine().connect() as conn:
        return [dict(r) for r in conn.execute(text(
            "SELECT id, organization_id, case_number FROM cases ORDER BY case_number"
        )).mappings()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill de hechos por parte")
    parser.add_argument("--case-id")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    if not args.case_id and not args.all:
        parser.error("Especifica --case-id o --all")

    rows = _case_rows()
    if args.case_id:
        rows = [r for r in rows if str(r["id"]) == args.case_id]
    total = 0
    for case in rows:
        if args.dry_run:
            with tx(str(case["organization_id"]), None) as conn:
                n = conn.execute(text("SELECT count(*) FROM facts WHERE case_id=:c"),
                                 {"c": str(case["id"])}).scalar()
            print(f"{case['case_number']}: {n} hechos (dry-run)")
            total += int(n or 0)
            continue
        with tx(str(case["organization_id"]), None) as conn:
            result = party_facts.rebuild_case(
                conn, str(case["organization_id"]), str(case["id"]), None,
                limit=args.limit)
        print(f"{case['case_number']}: {result}")
        total += result.get("facts", 0)
    print(f"Total: {total} hechos procesados")


if __name__ == "__main__":
    main()
