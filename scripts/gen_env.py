#!/usr/bin/env python3
"""Genera .env (o el archivo indicado) a partir de .env.example, reemplazando
__GENERATE__ por secretos aleatorios criptográficamente seguros. Nunca sobrescribe
un archivo existente salvo con --force.

Uso / Usage:
  python scripts/gen_env.py                         # -> .env
  python scripts/gen_env.py --out .env.test --set APP_ENV=test --set POSTGRES_DB=judicial_test
"""
from __future__ import annotations

import argparse
import secrets
import string
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def strong_password(n: int = 20) -> str:
    # garantiza mayúscula, minúscula, dígito y símbolo (política PASSWORD_MIN_LENGTH)
    alphabet = string.ascii_letters + string.digits + "-_.!@%"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
                and any(c in "-_.!@%" for c in pw)):
            return pw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".env")
    ap.add_argument("--set", action="append", default=[], help="KEY=VALUE overrides")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = ROOT / a.out
    if out.exists() and not a.force:
        print(f"[gen_env] {out.name} already exists; use --force to regenerate")
        return
    overrides = dict(kv.split("=", 1) for kv in a.set)
    lines = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, rest = line.split("=", 1)
            val, _, comment = rest.partition(" #")
            k = k.strip()
            if k in overrides:
                val = overrides.pop(k)
            elif val.strip() == "__GENERATE__":
                val = secrets.token_urlsafe(48) if k == "JWT_SECRET" else strong_password()
            line = f"{k}={val.strip()}" + (f"  #{comment}" if comment else "")
        lines.append(line)
    for k, v in overrides.items():
        lines.append(f"{k}={v}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out.chmod(0o600)
    print(f"[gen_env] wrote {out.name} (permissions 600). Secrets are random; keep this file out of Git.")


if __name__ == "__main__":
    main()
