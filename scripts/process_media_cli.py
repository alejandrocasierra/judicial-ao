#!/usr/bin/env python3
"""Procesa medios (ASR + diarización + identificación visual) — CLI admin (Fase 3).

Uso (dentro del contenedor worker o con acceso a BD/storage):

    python scripts/process_media_cli.py \
        --media-id <uuid> [--media-id <uuid> ...] \
        --case-id <uuid> --org-id <uuid> --user-id <uuid>

No es un endpoint público: sigue el mismo patrón que import_expediente.py.
Cada medio se procesa en su propia transacción; un fallo no aborta los demás.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Permitir importar app.* desde apps/api
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# En Docker las variables ya vienen del env_file del compose; .env no se copia
# a la imagen. Solo cargamos .env si existe (ejecución local).
_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    import envload  # noqa: E402

    envload.load(str(_env_path))

from app.core.db import tx  # noqa: E402
from app.services.media_pipeline import process_media  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Procesa medios con ASR + diarización + visual ID")
    p.add_argument("--media-id", action="append", required=True, dest="media_ids")
    p.add_argument("--case-id", required=True)
    p.add_argument("--org-id", required=True)
    p.add_argument("--user-id", required=True)
    args = p.parse_args()

    failures = 0
    for media_id in args.media_ids:
        t0 = time.time()
        try:
            with tx(args.org_id, args.user_id) as conn:
                result = process_media(conn, media_id, args.org_id, args.case_id, args.user_id)
            elapsed = time.time() - t0
            print(f"[OK] media={media_id} elapsed={elapsed:.0f}s result={result}", flush=True)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            import traceback
            print(f"[FAIL] media={media_id}: {exc}", flush=True)
            traceback.print_exc()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
