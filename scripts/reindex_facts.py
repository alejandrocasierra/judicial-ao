"""Reindexa los chunks estructurados (incluido el ledger de hechos).

Uso:
  python scripts/reindex_facts.py --case-id <uuid>
  python scripts/reindex_facts.py --all --local-hash
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
from app.services import indexing  # noqa: E402


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variable de entorno requerida: {name}")
    return value


def _owner_engine():
    url = (f"postgresql+psycopg://{_env('POSTGRES_SUPERUSER')}:{quote_plus(_env('POSTGRES_SUPERUSER_PASSWORD'))}"
           f"@{_env('POSTGRES_HOST')}:{_env('POSTGRES_PORT')}/{_env('POSTGRES_DB')}")
    return create_engine(url)


def _cases():
    with _owner_engine().connect() as conn:
        return [dict(r) for r in conn.execute(text(
            "SELECT id, organization_id, case_number FROM cases ORDER BY case_number"
        )).mappings()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Reindexar chunks estructurados de hechos")
    parser.add_argument("--case-id")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--local-hash", action="store_true",
                        help="Fija EMBEDDING_PROVIDER=local-hash y local-ngram-1024")
    args = parser.parse_args()
    if not args.case_id and not args.all:
        parser.error("Especifica --case-id o --all")
    if args.local_hash:
        os.environ["EMBEDDING_PROVIDER"] = "local-hash"
        os.environ["EMBEDDING_MODEL"] = "local-ngram-1024"

    cases = _cases()
    if args.case_id:
        cases = [c for c in cases if str(c["id"]) == args.case_id]
    for case in cases:
        with tx(str(case["organization_id"]), None) as conn:
            result = indexing.index_structured(conn, str(case["organization_id"]), str(case["id"]))
        print(f"{case['case_number']}: {result}")


if __name__ == "__main__":
    main()
