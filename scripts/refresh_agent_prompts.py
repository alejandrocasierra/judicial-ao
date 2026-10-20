#!/usr/bin/env python3
"""Actualiza el prompt sembrado del agente jurídico de v7 a v8.

Solo toca agentes de sistema cuyo `system_prompt` coincide exactamente con el
texto v7 (es decir, no fueron editados por la organización). Con `--force`
actualiza todos los agentes de sistema del nombre indicado.

Uso:
  ENV_FILE=.env.develop python scripts/refresh_agent_prompts.py --dry-run
  ENV_FILE=.env.develop python scripts/refresh_agent_prompts.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

if os.environ.get("ENV_FILE"):
    import envload  # noqa: E402
    envload.load(os.environ["ENV_FILE"], override=False)
elif (_ROOT / ".env").exists():
    import envload  # noqa: E402
    envload.load(str(_ROOT / ".env"))

from app.core.config import get_settings  # noqa: E402
from app.services import builtin_agents  # noqa: E402
from app.services.legal_extraction import _load_prompt  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--name", default=builtin_agents.BUILTIN_AGENT_NAME)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    new_prompt = builtin_agents.default_agent_prompt("es")
    old_prompt, _, _ = _load_prompt("agent_system", "es", version=7)
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
            cur.execute("""SELECT id, organization_id, name, system_prompt
                           FROM agents WHERE is_system = true AND name = %s""", (args.name,))
            for row in cur.fetchall():
                current = (row["system_prompt"] or "").strip()
                if not args.force and current != old_prompt.strip():
                    print(f"skip {row['id']} (prompt editado por la organización)")
                    continue
                changed += 1
                print(("would update " if args.dry_run else "update ") + str(row["id"]))
                if not args.dry_run:
                    cur.execute("UPDATE agents SET system_prompt = %s WHERE id = %s",
                                (new_prompt, row["id"]))
    finally:
        conn.close()
    print(f"agentes_actualizados={changed} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
