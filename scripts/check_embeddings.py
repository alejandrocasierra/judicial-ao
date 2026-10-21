#!/usr/bin/env python3
"""Comprueba si el proveedor de embeddings configurado TIENE espacio semántico.

No se fía del nombre del proveedor: mide pares de paráfrasis (misma intención,
poco solape léxico) contra pares de intención distinta. Ese margen es lo que
autoriza al perfil de intención a pasar de `shadow` a `active`.

    python scripts/check_embeddings.py
    python scripts/check_embeddings.py --require-semantic   # exit 1 si no lo es
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
    p = argparse.ArgumentParser(description="Sonda de capacidad semántica de los embeddings.")
    p.add_argument("--require-semantic", action="store_true",
                   help="Sale con 1 si el proveedor no separa paráfrasis (perfil en sombra)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    env_path = Path(os.environ.get("ENV_FILE") or (_ROOT / ".env"))
    if env_path.exists():
        import envload

        envload.load(str(env_path), override=bool(os.environ.get("ENV_FILE")))

    from app.core.config import get_settings
    from app.services.intent_profile import intent_embedding_provider, probe_embeddings

    settings = get_settings()
    result = probe_embeddings(intent_embedding_provider(settings))
    result["dimensions"] = settings.EMBEDDING_DIMENSIONS

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"proveedor : {result['provider']}  modelo: {result['model'] or '(sin modelo)'}")
        print(f"dimensiones: {result['dimensions']}")
        print(f"paráfrasis : {result['same']:.3f}   intención distinta: {result['different']:.3f}")
        print(f"margen     : {result['gap']:+.3f}   (mínimos: same≥0.55, gap≥0.08)")
        verdict = "SÍ separa intención" if result["semantic"] else "NO separa intención (perfil en sombra)"
        print(f"resultado  : {verdict}")
        if not result["semantic"]:
            print("\nPara activar el perfil configura un modelo semántico de 1024 dimensiones:")
            print("  EMBEDDING_PROVIDER=sentence-transformers")
            print("  EMBEDDING_MODEL=BAAI/bge-m3")
            print("o EMBEDDING_PROVIDER=openai con EMBEDDING_MODEL=text-embedding-3-small")
    if args.require_semantic and not result["semantic"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
