#!/usr/bin/env python3
"""Bootstrap mínimo de PRODUCCIÓN: organización + administrador + agentes/skills de sistema.

NO crea expedientes ni datos de demo. Es idempotente.

    python -m seeds.bootstrap_admin        (dentro del contenedor api)

Variables de entorno:
    BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD, BOOTSTRAP_ADMIN_NAME
    BOOTSTRAP_ORG_NAME (def. "Organización"), BOOTSTRAP_ORG_SLUG (def. "principal")
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "api"))
import envload  # noqa: E402

# Carga el archivo ENV_FILE si existe (host); en contenedor se usan las env de compose.
_env_file = Path(os.environ["ENV_FILE"]) if os.environ.get("ENV_FILE") else ROOT / ".env"
if not _env_file.is_absolute():
    _env_file = ROOT / _env_file
if _env_file.exists():
    envload.load(str(_env_file), override=True)

from sqlalchemy import text  # noqa: E402

from app.core.db import one, rows, tx  # noqa: E402
from app.security.passwords import hash_password, password_policy_ok  # noqa: E402
from app.services import builtin_agents  # noqa: E402


def _admin_email() -> str:
    return (os.environ.get("BOOTSTRAP_ADMIN_EMAIL") or "").strip().lower()


def _orgs() -> list[str]:
    with tx(None) as c:
        return [str(o["id"]) for o in rows(c, "SELECT id FROM ops_list_organizations() ORDER BY name")]


def _find_org_with_email(email: str) -> str | None:
    """El email es único GLOBAL: busca en todas las orgs (RLS sólo deja ver la actual)."""
    if not email:
        return None
    for org_id in _orgs():
        with tx(org_id) as c:
            if one(c, "SELECT id FROM users WHERE email = :e", e=email):
                return org_id
    return None


def _ensure_org() -> str:
    slug = (os.environ.get("BOOTSTRAP_ORG_SLUG") or "").strip()
    with tx(None) as c:
        org = one(c, "SELECT id FROM ops_list_organizations() WHERE slug = :s LIMIT 1", s=slug) if slug else None
        if not org:
            org = one(c, "SELECT id FROM ops_list_organizations() ORDER BY name LIMIT 1")
    if org:
        return str(org["id"])
    org_id = str(uuid.uuid4())
    name = (os.environ.get("BOOTSTRAP_ORG_NAME") or "Organización").strip()
    slug = slug or "principal"
    with tx(org_id) as c:
        c.execute(text("INSERT INTO organizations (id, name, slug, default_locale) VALUES (:i,:n,:s,'es')"),
                  {"i": org_id, "n": name, "s": slug})
    print(f"[bootstrap] organización creada: {name} ({slug})")
    return org_id


def _ensure_admin(org_id: str) -> None:
    email = (os.environ.get("BOOTSTRAP_ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or ""
    name = (os.environ.get("BOOTSTRAP_ADMIN_NAME") or "Administrador").strip()
    if not email or not password:
        print("[bootstrap] BOOTSTRAP_ADMIN_EMAIL/PASSWORD no definidos: no se crea administrador")
        return
    if not password_policy_ok(password):
        print("[bootstrap] BOOTSTRAP_ADMIN_PASSWORD no cumple la política: omitido")
        return
    with tx(org_id) as c:
        existing = one(c, "SELECT id FROM users WHERE email = :e", e=email)
        if existing:
            c.execute(text("""UPDATE users SET password_hash = :h, full_name = :n, org_role = 'ORG_ADMIN',
                              is_active = true, failed_login_attempts = 0, locked_until = NULL WHERE id = :u"""),
                      {"h": hash_password(password), "n": name, "u": str(existing["id"])})
        else:
            c.execute(text("""INSERT INTO users (organization_id, email, full_name, password_hash, org_role, locale)
                              VALUES (:o, :e, :n, :h, 'ORG_ADMIN', 'es')"""),
                      {"o": org_id, "e": email, "n": name, "h": hash_password(password)})
    print(f"[bootstrap] administrador listo: {email}")


def _ensure_agents(org_id: str) -> None:
    with tx(org_id) as c:
        builtin_agents.ensure_builtin_agents(c, org_id, None)
        builtin_agents.ensure_task_agents(c, org_id, None)
        builtin_agents.ensure_chat_agents(c, org_id, None)
    print("[bootstrap] agentes y skills de sistema listos")


def main() -> None:
    org_id = _find_org_with_email(_admin_email()) or _ensure_org()
    _ensure_admin(org_id)
    _ensure_agents(org_id)
    print("[bootstrap] listo. Inicia sesión y configura Modelos IA (API keys) en el dashboard.")


if __name__ == "__main__":
    main()
