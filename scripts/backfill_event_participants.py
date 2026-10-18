"""Backfill: vincula actuaciones procesales existentes con las partes del caso.

Cada evento con `actor` canónico (demandante, demandado, ...) se actualiza para que
su array `participants` incluya las partes del caso con el rol correspondiente. Es
idempotente: conserva los participantes que ya tuviera y agrega los que faltan.

Uso:
    python scripts/backfill_event_participants.py --case-id <uuid>
    python scripts/backfill_event_participants.py --all  # todos los casos (cuidado en prod)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import create_engine, text

from app.services.procedural_actor_roles import ACTOR_ROLE_MAP


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Variable de entorno requerida: {name}")
    return v


def backfill_case(conn, case_id: str, dry_run: bool = False) -> int:
    role_map = {k: v for k, v in ACTOR_ROLE_MAP.items() if v is not None}
    actors = list(role_map.keys())
    rows = conn.execute(text("""
        SELECT e.id, e.actor, e.participants
        FROM events e
        WHERE e.case_id = :case_id
          AND e.kind = 'procedural'
          AND e.actor = ANY(:actors)
        ORDER BY e.id
    """), {"case_id": case_id, "actors": actors}).mappings().all()

    updated = 0
    for ev in rows:
        role_code = role_map.get(ev["actor"])
        if not role_code:
            continue
        party_rows = conn.execute(text("""
            SELECT id FROM parties
            WHERE case_id = :case_id AND role = :role
        """), {"case_id": case_id, "role": role_code}).mappings().all()
        pids = {str(p["id"]) for p in party_rows}
        existing = {str(x) for x in (ev["participants"] or []) if x}
        merged = sorted(existing | pids)
        if merged == sorted(existing):
            continue
        if not dry_run:
            conn.execute(text("""
                UPDATE events SET participants = CAST(:pids AS uuid[])
                WHERE id = :eid AND case_id = :case_id
            """), {"pids": merged, "eid": str(ev["id"]), "case_id": case_id})
        updated += 1
    return updated


def all_case_ids(conn):
    return [r["id"] for r in conn.execute(text("SELECT id FROM cases")).mappings()]


def main():
    parser = argparse.ArgumentParser(description="Backfill de participants en events")
    parser.add_argument("--case-id", help="UUID del caso a procesar")
    parser.add_argument("--all", action="store_true", help="Procesar todos los casos")
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar cuántos se actualizarían")
    args = parser.parse_args()

    if not args.case_id and not args.all:
        parser.error("Especifica --case-id o --all")

    db_url = (
        f"postgresql+psycopg://{_env('DB_APP_USER')}:{_env('DB_APP_PASSWORD')}"
        f"@{_env('POSTGRES_HOST')}:{_env('POSTGRES_PORT')}/{_env('POSTGRES_DB')}"
    )
    engine = create_engine(db_url)

    with engine.connect() as conn:
        case_ids = [args.case_id] if args.case_id else all_case_ids(conn)
        total = 0
        for cid in case_ids:
            n = backfill_case(conn, cid, dry_run=args.dry_run)
            print(f"{cid}: {n} eventos {'se actualizarían' if args.dry_run else 'actualizados'}")
            total += n
        if not args.dry_run:
            conn.commit()
        print(f"Total: {total}")


if __name__ == "__main__":
    main()
