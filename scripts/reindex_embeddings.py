#!/usr/bin/env python3
"""Reindexa embeddings cuando el modelo/versiÃ³n del Ã­ndice no coincide con el entorno.

Uso:
  ENV_FILE=.env.develop python scripts/reindex_embeddings.py --dry-run
  ENV_FILE=.env.develop python scripts/reindex_embeddings.py --all
  ENV_FILE=.env.develop python scripts/reindex_embeddings.py --case-id <uuid>

DiseÃ±o:
- Detecta por caso la combinaciÃ³n (embedding_model, embedding_version) de `chunks`.
- Si no coincide con `EMBEDDING_MODEL`/`PIPELINE_VERSION`, reindexa el caso completo
  con `indexing.index_case` (idempotente: borra y recrea sus chunks).
- `--dry-run` imprime el plan sin borrar ni llamar al proveedor.
- Sale con cÃ³digo 1 si quedan casos desalineados despuÃ©s de `--all`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

if not os.environ.get("ENV_FILE") and (_ROOT / ".env").exists():
    import envload  # noqa: E402
    envload.load(str(_ROOT / ".env"))

from app.core.config import PLACEHOLDERS, get_settings  # noqa: E402
from app.core.db import rows, tx  # noqa: E402
from app.services import indexing  # noqa: E402


def _owner_plan(model: str, version: str) -> list[dict]:
    """DiagnÃ³stico global con el rol owner (BYPASSRLS): el rol app no ve todos los casos."""
    import psycopg
    from psycopg.rows import dict_row

    s = get_settings()
    conn = psycopg.connect(host=s.POSTGRES_HOST, port=s.POSTGRES_PORT, dbname=s.POSTGRES_DB,
                           user=os.environ["DB_OWNER_USER"], password=os.environ["DB_OWNER_PASSWORD"],
                           row_factory=dict_row, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.id AS case_id, c.organization_id,
                       min(ch.embedding_model) AS current_model,
                       min(ch.embedding_version) AS current_version,
                       count(ch.id) AS chunks
                FROM cases c
                JOIN chunks ch ON ch.case_id = c.id
                GROUP BY c.id, c.organization_id
                HAVING bool_or(ch.embedding_model IS DISTINCT FROM %s
                               OR ch.embedding_version IS DISTINCT FROM %s)
                ORDER BY c.id
            """, (model, version))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Reindexa embeddings desalineados")
    p.add_argument("--case-id", type=UUID, default=None)
    p.add_argument("--all", action="store_true", help="reindexar todos los casos desalineados")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--user-id", type=UUID, default=None)
    args = p.parse_args()

    s = get_settings()
    model = s.EMBEDDING_MODEL
    version = s.PIPELINE_VERSION
    if model in PLACEHOLDERS:
        print(f"[ERROR] EMBEDDING_MODEL={model!r} es placeholder; configura el modelo real.")
        return 2

    plan = _owner_plan(model, version)
    if args.case_id:
        plan = [row for row in plan if str(row["case_id"]) == str(args.case_id)]
    print(f"modelo={model} version={version} casos_desalineados={len(plan)}")
    for row in plan:
        print(f"  case={row['case_id']} org={row['organization_id']} "
              f"chunks={row['chunks']} actual={row['current_model']}/{row['current_version']}")
    if args.dry_run or not plan:
        return 0 if not plan else 1
    if not args.all and not args.case_id:
        print("[ERROR] usa --all o --case-id")
        return 2

    failed = 0
    for row in plan:
        org_id = str(row["organization_id"])
        with tx(org_id, str(args.user_id) if args.user_id else None) as conn:
            user = rows(conn, "SELECT id FROM users WHERE organization_id = :o ORDER BY created_at LIMIT 1",
                        o=org_id)
            user_id = str(args.user_id) if args.user_id else (str(user[0]["id"]) if user else None)
        try:
            with tx(org_id, user_id) as conn:
                result = indexing.index_case(conn, org_id, str(row["case_id"]), user_id)
            print(f"[OK] case={row['case_id']} {result}")
        except Exception as exc:  # noqa: BLE001 â€” seguir con los demÃ¡s casos
            failed += 1
            print(f"[FAIL] case={row['case_id']} {type(exc).__name__}: {exc}")

    remaining = _owner_plan(model, version)
    print(f"restantes_desalineados={len(remaining)} fallos={failed}")
    return 1 if failed or remaining else 0


if __name__ == "__main__":
    raise SystemExit(main())

