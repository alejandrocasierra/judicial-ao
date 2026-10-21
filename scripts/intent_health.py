#!/usr/bin/env python3
"""Salud del perfil de intención: perfiles degradados o con más FP de `navegar`.

El job nocturno degrada solo cuando un perfil empeora. Esto lo hace visible y
sale con código 1 (para un cron externo, CI o una alerta). No modifica nada.

    python scripts/intent_health.py
    python scripts/intent_health.py --org-id <uuid>
    python scripts/intent_health.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Perfiles de intención degradados.")
    p.add_argument("--org-id", default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    env_path = Path(os.environ.get("ENV_FILE") or (_ROOT / ".env"))
    if env_path.exists():
        import envload

        envload.load(str(env_path), override=bool(os.environ.get("ENV_FILE")))

    from app.services import intent_learning

    rows = intent_learning.health(args.org_id)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
    elif not rows:
        print("Salud del perfil de intención: sin degradaciones.")
    else:
        print(f"{'org':36} {'usuario':38} {'estado':7} {'FP perfil/reglas':>16}  motivo")
        for row in rows:
            print(f"{str(row.get('org_id')):36} {str(row.get('user_id')):38} "
                  f"{str(row.get('status')):7} "
                  f"{int(row.get('navegar_fp_profile') or 0):>7}/{int(row.get('navegar_fp_rules') or 0):<8}  "
                  f"{row.get('reason') or ''}")
    return 1 if rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
