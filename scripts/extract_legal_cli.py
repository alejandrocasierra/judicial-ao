#!/usr/bin/env python3
"""Ejecuta extracción jurídica con LLM sobre documentos/media — CLI admin (Fase 4).

Uso (dentro del contenedor worker o con acceso a BD):

    python scripts/extract_legal_cli.py \
        --source-id <uuid> [--source-id <uuid> ...] \
        --case-id <uuid> --org-id <uuid> --user-id <uuid>

Cada fuente se resuelve automáticamente como documento o media.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    import envload  # noqa: E402

    envload.load(str(_env_path))

from app.workers.handlers.legal_extraction import handle  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Extrae entidades, claims, eventos, decisiones y contradicciones")
    p.add_argument("--source-id", action="append", required=True, dest="source_ids")
    p.add_argument("--case-id", required=True)
    p.add_argument("--org-id", required=True)
    p.add_argument("--user-id", required=True)
    args = p.parse_args()

    job = {
        "organization_id": args.org_id,
        "case_id": args.case_id,
        "created_by": args.user_id,
        "input_ids": args.source_ids,
    }

    t0 = time.time()
    try:
        result = handle(job)
        elapsed = time.time() - t0
        print(f"[OK] elapsed={elapsed:.0f}s result={result}", flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"[FAIL] {exc}", flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
