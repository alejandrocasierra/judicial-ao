"""Backfill: reindexa chunks de medios para incluir asr_mode en metadata.

Uso:
    python scripts/backfill_asr_mode_chunks.py --case-id <uuid>
    python scripts/backfill_asr_mode_chunks.py --all  # todos los casos
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import create_engine, text

from app.core.db import tx
from app.services import indexing


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Variable de entorno requerida: {name}")
    return v


def _engine(superuser: bool = False):
    if superuser:
        db_url = (
            f"postgresql+psycopg://{_env('POSTGRES_SUPERUSER')}:{quote_plus(_env('POSTGRES_SUPERUSER_PASSWORD'))}"
            f"@{_env('POSTGRES_HOST')}:{_env('POSTGRES_PORT')}/{_env('POSTGRES_DB')}"
        )
    else:
        db_url = (
            f"postgresql+psycopg://{_env('DB_APP_USER')}:{quote_plus(_env('DB_APP_PASSWORD'))}"
            f"@{_env('POSTGRES_HOST')}:{_env('POSTGRES_PORT')}/{_env('POSTGRES_DB')}"
        )
    return create_engine(db_url)


def all_case_ids():
    # Conexión como superusuario para evitar RLS.
    with _engine(superuser=True).connect() as conn:
        return [str(r["id"]) for r in conn.execute(text("SELECT id FROM cases")).mappings()]


def backfill_case(case_id: str) -> int:
    # Obtenemos el org_id como superusuario para poder invocar tx(org_id).
    with _engine(superuser=True).connect() as conn:
        org_row = conn.execute(text("SELECT organization_id FROM cases WHERE id = :c"), {"c": case_id}).mappings().first()
    if not org_row:
        return 0
    org_id = str(org_row["organization_id"])

    with tx(org_id) as c:
        media = c.execute(text("SELECT id FROM media WHERE case_id = :c"), {"c": case_id}).mappings().all()
        for m in media:
            indexing.index_media(c, org_id, case_id, str(m["id"]))
        return len(media)


def main():
    parser = argparse.ArgumentParser(description="Reindexar chunks de medios con asr_mode")
    parser.add_argument("--case-id", help="UUID del caso a procesar")
    parser.add_argument("--all", action="store_true", help="Procesar todos los casos")
    args = parser.parse_args()

    if not args.case_id and not args.all:
        parser.error("Especifica --case-id o --all")

    case_ids = [args.case_id] if args.case_id else all_case_ids()
    total_media = 0
    for cid in case_ids:
        n = backfill_case(cid)
        print(f"{cid}: {n} medios reindexados")
        total_media += n
    print(f"Total: {total_media} medios reindexados")


if __name__ == "__main__":
    main()
