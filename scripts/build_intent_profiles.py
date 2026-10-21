#!/usr/bin/env python3
"""Reconstruye el perfil de intención por usuario (lo mismo que el job nocturno).

El job `intent.rebuild_profiles` corre solo cada 24 h (Celery beat). Este CLI
sirve para el arranque/backfill y para inspeccionar qué haría:

    python scripts/build_intent_profiles.py --org-id <uuid> --dry-run
    python scripts/build_intent_profiles.py --org-id <uuid>
    python scripts/build_intent_profiles.py --all

Salida por usuario: ejemplos, turnos juzgados, accuracy de reglas vs perfil,
falsos positivos de `navegar` y el estado final (`off` / `shadow` / `active`).
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
    p = argparse.ArgumentParser(description="Reconstruye el perfil de intención por usuario.")
    p.add_argument("--org-id", default=None)
    p.add_argument("--user-id", default=None)
    p.add_argument("--all", action="store_true", help="Todas las organizaciones")
    p.add_argument("--days", type=int, default=90, help="Ventana de contrafactuales (default 90)")
    p.add_argument("--dry-run", action="store_true", help="No escribe nada")
    p.add_argument("--purge", action="store_true", help="Aplica la retención y sale")
    p.add_argument("--check", action="store_true", help="Sale con 1 si hay perfiles degradados")
    p.add_argument("--json", action="store_true", help="Salida JSON completa")
    args = p.parse_args()

    env_path = Path(os.environ.get("ENV_FILE") or (_ROOT / ".env"))
    if env_path.exists():
        import envload

        envload.load(str(env_path), override=bool(os.environ.get("ENV_FILE")))

    from app.services import intent_learning
    from app.services.intent_profile import embedding_capability

    if args.check:
        bad = intent_learning.health(args.org_id)
        if args.json:
            print(json.dumps(bad, indent=2, ensure_ascii=False, default=str))
        else:
            print("Salud del perfil: " + ("sin degradaciones." if not bad else f"{len(bad)} perfiles degradados."))
            for row in bad:
                print(f"  {row.get('user_id')} {row.get('status')} {row.get('reason')}")
        return 1 if bad else 0

    if args.purge:
        orgs = (intent_learning.organizations() if args.all
                else ([args.org_id] if args.org_id else []))
        if not orgs:
            print("[ERROR] falta --org-id (o usa --all)")
            return 2
        total = {"exemplars": 0, "profiles": 0}
        for org in orgs:
            purged = intent_learning.purge_expired(org)
            total["exemplars"] += purged["exemplars"]
            total["profiles"] += purged["profiles"]
        print(f"Retención aplicada: {total['exemplars']} ejemplos y {total['profiles']} perfiles borrados.")
        return 0

    if args.all:
        result = intent_learning.rebuild_all(days=args.days)
        report = result["report"]
    elif args.org_id:
        report = intent_learning.rebuild(args.org_id, user_id=args.user_id,
                                         days=args.days, dry_run=args.dry_run)
    else:
        print("[ERROR] falta --org-id (o usa --all)")
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0

    probe = embedding_capability()
    print(f"Embeddings: {probe['provider']} · same={probe['same']:.2f} diff={probe['different']:.2f} "
          f"gap={probe['gap']:+.2f} → {'semántico' if probe['semantic'] else 'NO semántico (sombra)'}")
    if not report:
        print("Sin usuarios con conducta que aprender (feedback o reformulaciones).")
        print("El perfil se activa solo cuando haya veredictos; hoy no los hay.")
        return 0

    print(f"{'usuario':38} {'ejemplos':>8} {'juzgados':>8} {'reglas':>7} {'perfil':>7} "
          f"{'FP nav':>7} {'estado':>7}  motivo")
    for row in report:
        print(f"{str(row.get('user_id')):38} {int(row.get('exemplars') or 0):8d} "
              f"{int(row.get('judged') or 0):8d} "
              f"{_pct(row.get('rules_accuracy')):>7} {_pct(row.get('profile_accuracy')):>7} "
              f"{int(row.get('navegar_fp_profile') or 0):>3}/{int(row.get('navegar_fp_rules') or 0):<3} "
              f"{str(row.get('status')):>7}  {row.get('reason') or ''}")
    return 0


def _pct(value) -> str:
    return "n/a" if value is None else f"{float(value):.0%}"


if __name__ == "__main__":
    raise SystemExit(main())
