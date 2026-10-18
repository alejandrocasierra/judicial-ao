"""Limpia jobs duplicados/rotos y reinicia el ASR de medios de un expediente.

Uso:
    JUDICIAL_ORG_ID=<uuid> JUDICIAL_USER_ID=<uuid> JUDICIAL_CASE_ID=<uuid> \
        python scripts/cleanup_and_restart_asr.py

    # Opcional: limitar a archivos concretos (separados por coma)
    JUDICIAL_MEDIA_NAMES="0083...mp4,0084...mp4" ...

Si no se define JUDICIAL_MEDIA_NAMES, toma los medios del caso que estén en
UPLOADED / ASR_PENDING / FAILED / ASR_RUNNING.

Acciones:
1. Cancela jobs activos (QUEUED/RUNNING/RETRYING) duplicados para cada media.
2. Pone cada media en ASR_PENDING.
3. Crea exactamente un job file_ingest fresco por media.
4. Encola los jobs para que el worker-media los procese de a uno.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from uuid import UUID

# Cargar .env
if not os.environ.get("JUDICIAL_ADMIN_PASSWORD"):
    try:
        from dotenv import load_dotenv
        load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.workers.dispatcher import create_job, enqueue_job

ORG = os.environ.get("JUDICIAL_ORG_ID", "")
USER = os.environ.get("JUDICIAL_USER_ID", "")
CASE = os.environ.get("JUDICIAL_CASE_ID", "")
if not (ORG and USER and CASE):
    raise SystemExit("Define JUDICIAL_ORG_ID, JUDICIAL_USER_ID y JUDICIAL_CASE_ID")

MEDIA_NAMES = [n.strip() for n in os.environ.get("JUDICIAL_MEDIA_NAMES", "").split(",") if n.strip()]


def main() -> int:
    s = get_settings()
    with tx(ORG, USER) as conn:
        if MEDIA_NAMES:
            media_rows = rows(conn, """
                SELECT id, filename, processing_status
                FROM media
                WHERE case_id = :c AND filename = ANY(:names)
                ORDER BY filename
            """, c=CASE, names=MEDIA_NAMES)
        else:
            media_rows = rows(conn, """
                SELECT id, filename, processing_status
                FROM media
                WHERE case_id = :c
                  AND processing_status IN ('UPLOADED', 'ASR_PENDING', 'FAILED', 'ASR_RUNNING')
                ORDER BY filename
            """, c=CASE)

    print(f"Media encontrados: {len(media_rows)}", flush=True)
    created_jobs: list[tuple[str, str]] = []

    for media in media_rows:
        print(f"Procesando {media['filename']}...", flush=True)
        mid = str(media["id"])
        filename = media["filename"]

        print(f"  [{filename}] buscando jobs activos...", flush=True)
        with tx(ORG, USER) as conn:
            # 1) Cancelar jobs activos asociados a este media
            active_jobs = rows(conn, """
                SELECT id, job_type, status, input_ids
                FROM jobs
                WHERE case_id = :c
                  AND status IN ('QUEUED', 'RUNNING', 'RETRYING')
                  AND input_ids @> ARRAY[:m]::uuid[]
                ORDER BY updated_at DESC
            """, c=CASE, m=mid)
            print(f"  [{filename}] {len(active_jobs)} jobs activos", flush=True)

            for job in active_jobs:
                one(conn, "UPDATE jobs SET status = 'CANCELLED', updated_at = now() WHERE id = :j RETURNING id", j=str(job["id"]))
                print(f"  [{filename}] cancelado {job['job_type']} {str(job['id'])[:8]} ({job['status']})", flush=True)

            # 2) Resetear estado del media a ASR_PENDING
            print(f"  [{filename}] reseteando media...", flush=True)
            one(conn, "UPDATE media SET processing_status = 'ASR_PENDING' WHERE id = :m RETURNING id", m=mid)

            # 3) Crear un job file_ingest fresco con clave única
            print(f"  [{filename}] creando job...", flush=True)
            ts = int(time.time())
            job = create_job(conn, org_id=ORG, case_id=CASE, job_type="file_ingest",
                             input_ids=[mid], actor_id=USER,
                             key_parts=["file_ingest", CASE, mid, "restart", ts],
                             pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
            print(f"  [{filename}] job creado {str(job['id'])[:8]}", flush=True)

        # 4) Encolar
        print(f"  [{filename}] encolando...", flush=True)
        enqueue_job(job["id"], ORG, USER, "file_ingest")
        created_jobs.append((filename, str(job["id"])))
        print(f"  [{filename}] listo", flush=True)

    print(f"\nTotal jobs creados: {len(created_jobs)}")
    print("El worker-media procesará un video a la vez.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
