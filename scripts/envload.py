"""Carga un archivo .env en os.environ sin dependencias (usado por scripts y Alembic).
Loads a .env file into os.environ (no dependencies)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDERS = {"__GENERATE__", "__SET_ME__"}


def load(path: str | None = None, override: bool = False) -> dict[str, str]:
    f = Path(path or os.environ.get("ENV_FILE") or ROOT / ".env")
    if not f.is_absolute():
        f = ROOT / f
    if not f.exists():
        raise SystemExit(f"[env] missing {f}. Run: python scripts/gen_env.py")
    values: dict[str, str] = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        values[k.strip()] = v
        if override or k.strip() not in os.environ:
            os.environ[k.strip()] = v
    os.environ.setdefault("ENV_FILE", str(f))
    return values


def require(*names: str) -> list[str]:
    missing = [n for n in names if not os.environ.get(n) or os.environ[n] in PLACEHOLDERS]
    if missing:
        raise SystemExit(f"[env] missing/placeholder variables: {', '.join(missing)}")
    return [os.environ[n] for n in names]


if __name__ == "__main__":
    import shlex
    import sys
    # Uso: eval "$(python3 scripts/envload.py --export .env)" — exporta variables con escape seguro para bash
    args = [a for a in sys.argv[1:] if a != "--export"]
    for k, v in load(args[0] if args else None, override=True).items():
        print(f"export {k}={shlex.quote(v)}")
