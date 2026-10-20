#!/usr/bin/env python3
"""Cifra en reposo secretos legacy de `ai_models` y `smtp_settings`.

Idempotente: no toca valores que ya llevan prefijo `enc:`. Se ejecuta una vez
tras desplegar el código nuevo y siempre que se importen datos antiguos.

Uso:
  ENV_FILE=.env.develop python scripts/encrypt_secrets.py --dry-run
  ENV_FILE=.env.develop python scripts/encrypt_secrets.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

if not os.environ.get("ENV_FILE") and (_ROOT / ".env").exists():
    import envload  # noqa: E402
    envload.load(str(_ROOT / ".env"))
elif os.environ.get("ENV_FILE"):
    import envload  # noqa: E402
    envload.load(os.environ["ENV_FILE"], override=False)

from app.core import secrets as secret_store  # noqa: E402
from app.core.config import get_settings  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    s = get_settings()

    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(host=s.POSTGRES_HOST, port=s.POSTGRES_PORT, dbname=s.POSTGRES_DB,
                           user=os.environ["DB_OWNER_USER"], password=os.environ["DB_OWNER_PASSWORD"],
                           row_factory=dict_row, connect_timeout=15)
    changed = 0
    try:
        with conn.transaction():
            cur = conn.cursor()
            cur.execute("SELECT id, encrypted_api_key FROM ai_models WHERE encrypted_api_key IS NOT NULL AND encrypted_api_key <> ''")
            for row in cur.fetchall():
                value = row["encrypted_api_key"]
                if secret_store.is_encrypted(value):
                    continue
                changed += 1
                if not args.dry_run:
                    cur.execute("UPDATE ai_models SET encrypted_api_key = %s WHERE id = %s",
                                (secret_store.encrypt(value), row["id"]))
            cur.execute("SELECT organization_id, password_encrypted FROM smtp_settings WHERE password_encrypted IS NOT NULL AND password_encrypted <> ''")
            for row in cur.fetchall():
                value = row["password_encrypted"]
                if secret_store.is_encrypted(value):
                    continue
                changed += 1
                if not args.dry_run:
                    cur.execute("UPDATE smtp_settings SET password_encrypted = %s WHERE organization_id = %s",
                                (secret_store.encrypt(value), row["organization_id"]))
    finally:
        conn.close()
    print(f"secretos_actualizados={changed} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
