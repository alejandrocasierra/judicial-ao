#!/usr/bin/env python3
"""Da de alta modelos de IA (Gemini por defecto) para una organización.

Idempotente: si el modelo ya existe (provider+model_name) actualiza key/base_url;
si no, lo crea. Deja uno marcado como `is_default`.

Uso:
    python scripts/seed_ai_models.py --org-id <uuid> --user-id <uuid> --api-key <key>
    python scripts/seed_ai_models.py --org-id <uuid> --user-id <uuid> --api-key <key> \
        --model gemini-2.5-pro --default gemini-2.5-pro

Dentro del contenedor:
    python /srv/scripts/seed_ai_models.py --org-id ... --user-id ... --api-key ...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

_env = Path(__file__).resolve().parents[1] / ".env"
if _env.exists():
    import envload  # noqa: E402

    envload.load(str(_env))

from sqlalchemy import text  # noqa: E402

from app.core.db import one, tx  # noqa: E402

DEFAULT_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODELS = [
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3.8-flash",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Alta de modelos de IA para una organización")
    ap.add_argument("--org-id", required=True, type=UUID)
    ap.add_argument("--user-id", required=True, type=UUID)
    ap.add_argument("--provider", default="gemini")
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--base-url", default=DEFAULT_GEMINI_BASE)
    ap.add_argument("--model", action="append", default=[], help="repetible; si se omite, usa la lista por defecto")
    ap.add_argument("--default", default=None, help="model_name a marcar como is_default")
    ap.add_argument("--no-default", action="store_true")
    a = ap.parse_args()

    models = a.model or DEFAULT_MODELS
    default_model = None if a.no_default else (a.default or models[0])
    org, user = str(a.org_id), str(a.user_id)
    created: list[str] = []
    updated: list[str] = []

    with tx(org, user) as c:
        for name in models:
            row = one(c, "SELECT id FROM ai_models WHERE organization_id = :o AND provider = :p AND model_name = :m",
                      o=org, p=a.provider, m=name)
            if row:
                c.execute(text("UPDATE ai_models SET encrypted_api_key = :k, api_base_url = :b, updated_at = now() WHERE id = :i"),
                          {"k": a.api_key, "b": a.base_url, "i": str(row["id"])})
                updated.append(name)
            else:
                one(c, """INSERT INTO ai_models (organization_id, provider, model_name, api_key_ref, encrypted_api_key,
                              api_base_url, is_default, created_by)
                    VALUES (:o, :p, :m, 'org', :k, :b, false, :u) RETURNING id""",
                    o=org, p=a.provider, m=name, k=a.api_key, b=a.base_url, u=user)
                created.append(name)
        if default_model:
            c.execute(text("UPDATE ai_models SET is_default = false WHERE organization_id = :o"), {"o": org})
            c.execute(text("UPDATE ai_models SET is_default = true WHERE organization_id = :o AND provider = :p AND model_name = :m"),
                      {"o": org, "p": a.provider, "m": default_model})

    print(f"[OK] creados={created} actualizados={updated} default={default_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
