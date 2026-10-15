#!/usr/bin/env python3
"""Limpia y reconstruye la línea de tiempo procesal de un caso.

Uso:
  python scripts/fix_timeline.py <case_number>
  python scripts/fix_timeline.py 11001310302120180036100

Qué hace:
  1. Borra eventos con fechas placeholder (1899-12-31, 1900-01-01, 0001-01-01).
  2. Borra eventos referenciados que ya tienen un evento real resuelto (duplicate_of).
  3. Limpia banderas de 'fecha_inconsistente' y 'duplicado' de eventos restantes.
  4. Encola re-extracción legal de todos los documentos/media del caso.
  5. Encola rebuild del Process Graph.

Requiere reiniciar la API/web ANTES para que corra el código nuevo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import envload  # noqa: E402

try:
    envload.load(override=True)
except SystemExit:
    pass

env, host, port, db, owner, owner_pw, app, app_pw = envload.require(
    "APP_ENV", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
    "DB_OWNER_USER", "DB_OWNER_PASSWORD", "DB_APP_USER", "DB_APP_PASSWORD")

PLACEBO_DATES = {"1899-12-31", "1899-12-30", "1900-01-01", "0001-01-01"}


def connect():
    import psycopg
    return psycopg.connect(host=host, port=port, user=owner, password=owner_pw,
                           dbname=db, autocommit=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_number", help="Número de radicado del caso")
    ap.add_argument("--dry-run", action="store_true", help="No borra nada, solo muestra conteos")
    args = ap.parse_args()

    if env not in ("development", "test"):
        print("[fix_timeline] ADVERTENCIA: APP_ENV no es development/test. Revisa antes de continuar.")

    with connect() as conn:
        # Buscar caso
        row = conn.execute(
            "SELECT id, title, organization_id FROM cases WHERE case_number = %s",
            (args.case_number,)
        ).fetchone()
        if not row:
            raise SystemExit(f"[fix_timeline] caso no encontrado: {args.case_number}")
        case_id, title, org_id = row
        print(f"[fix_timeline] caso: {title} / {args.case_number} ({case_id})")

        # RLS: fijar organización para que las operaciones vean solo las filas del tenant
        conn.execute("SELECT set_config('app.current_org', %s, true)", (str(org_id),))
        conn.execute("SELECT set_config('app.current_actor', %s, true)", (os.environ.get("FIX_TIMELINE_ACTOR_ID", "00000000-0000-0000-0000-000000000000"),))

        # Conteos previos
        counts = conn.execute("""
            SELECT
              count(*) FILTER (WHERE event_date::text IN %s) AS placebo,
              count(*) FILTER (WHERE date_type = 'referenciada' AND duplicate_of IS NOT NULL) AS resolved_refs,
              count(*) FILTER (WHERE review_flags @> '["fecha_inconsistente"]') AS inconsistent,
              count(*) FILTER (WHERE review_flags @> '["duplicado"]') AS dup
            FROM events WHERE case_id = %s
        """, (tuple(PLACEBO_DATES), str(case_id))).fetchone()
        print(f"[fix_timeline] antes -> fechas placeholder: {counts[0]}, referenciados resueltos: {counts[1]}, "
              f"fecha_inconsistente: {counts[2]}, duplicado: {counts[3]}")

        if args.dry_run:
            print("[fix_timeline] dry-run: no se aplicaron cambios")
            return

        # 1. Borrar eventos con fechas placeholder
        conn.execute("""
            DELETE FROM events
            WHERE case_id = %s AND event_date::text IN %s
        """, (str(case_id), tuple(PLACEBO_DATES)))
        print("[fix_timeline] + borradas fechas placeholder")

        # 2. Borrar eventos referenciados resueltos (el real ya existe)
        conn.execute("""
            DELETE FROM events
            WHERE case_id = %s AND date_type = 'referenciada' AND duplicate_of IS NOT NULL
        """, (str(case_id),))
        print("[fix_timeline] + borrados referenciados resueltos")

        # 3. Limpiar banderas de review_flags
        conn.execute("""
            UPDATE events
            SET review_flags = (
              SELECT COALESCE(jsonb_agg(value), '[]'::jsonb)
              FROM jsonb_array_elements(review_flags) AS value
              WHERE value::text NOT IN ('"fecha_inconsistente"', '"duplicado"')
            )
            WHERE case_id = %s AND review_flags @> '["fecha_inconsistente"]' OR review_flags @> '["duplicado"]'
        """, (str(case_id),))
        print("[fix_timeline] + limpiadas banderas")

        # 4. Encolar re-extracción legal de todos los documentos y media
        docs = conn.execute(
            "SELECT id FROM documents WHERE case_id = %s",
            (str(case_id),)
        ).fetchall()
        media = conn.execute(
            "SELECT id FROM media WHERE case_id = %s",
            (str(case_id),)
        ).fetchall()
        source_ids = [str(r[0]) for r in docs + media]
        if source_ids:
            _enqueue_job(conn, org_id, case_id, "legal_extraction", source_ids)
            print(f"[fix_timeline] + encolada legal_extraction: {len(source_ids)} fuentes")

        # 5. Encolar rebuild del Process Graph
        _enqueue_job(conn, org_id, case_id, "procedural_links", [])
        print("[fix_timeline] + encolado procedural_links")

        conn.commit()

    print("[fix_timeline] listo. El worker procesará los jobs en segundo plano.")
    print("[fix_timeline] reinicia la API/web si aún no lo hiciste para que el código nuevo esté activo.")


def _enqueue_job(conn, org_id: str, case_id: str, job_type: str, input_ids: list[str]) -> None:
    """Inserta un job idempotente en la tabla jobs (el worker lo recogerá)."""
    key_parts = [job_type, str(case_id), sorted(input_ids) if input_ids else int(time.time() // 60)]
    key = hashlib.sha256(json.dumps(key_parts, ensure_ascii=False).encode()).hexdigest()
    pipeline_version = os.environ.get("PIPELINE_VERSION", "1.0")
    model_version = os.environ.get("LLM_MODEL", "unknown")
    actor_id = os.environ.get("FIX_TIMELINE_ACTOR_ID", "00000000-0000-0000-0000-000000000000")
    conn.execute("""
        INSERT INTO jobs (organization_id, case_id, job_type, input_ids, idempotency_key,
                          pipeline_version, model_version, created_by, status)
        VALUES (%s, %s, %s, %s::uuid[], %s, %s, %s, %s, 'QUEUED')
        ON CONFLICT (organization_id, idempotency_key) DO NOTHING
    """, (str(org_id), str(case_id), job_type,
          "{" + ",".join(input_ids) + "}" if input_ids else "{}",
          key, pipeline_version, model_version, actor_id))


if __name__ == "__main__":
    main()
