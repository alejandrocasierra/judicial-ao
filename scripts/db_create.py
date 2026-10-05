#!/usr/bin/env python3
"""Crea roles, base de datos y extensiones usando el superusuario (sólo bootstrap).
Creates roles, database and extensions with the superuser (bootstrap only).

  python scripts/db_create.py            # idempotente
  python scripts/db_create.py --drop     # SOLO APP_ENV in (development,test)
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg import sql

sys.path.insert(0, os.path.dirname(__file__))
import envload  # noqa: E402

try:
    envload.load(override=True)  # el archivo ENV_FILE es la fuente de verdad
except SystemExit:
    pass  # en contenedor (sin /srv/.env) valen las variables de entorno del compose
env, host, port, su, su_pw, db, owner, owner_pw, app, app_pw = envload.require(
    "APP_ENV", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_SUPERUSER", "POSTGRES_SUPERUSER_PASSWORD", "POSTGRES_DB",
    "DB_OWNER_USER", "DB_OWNER_PASSWORD", "DB_APP_USER", "DB_APP_PASSWORD")


def conn(dbname: str):
    return psycopg.connect(host=host, port=port, user=su, password=su_pw, dbname=dbname, autocommit=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true")
    a = ap.parse_args()
    with conn("postgres") as c:
        if a.drop:
            if env not in ("development", "test"):
                raise SystemExit("[db_create] --drop refused: APP_ENV is not development/test")
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
            print(f"[db_create] dropped {db}")
        for role, pw, extra in ((owner, owner_pw, sql.SQL("BYPASSRLS")), (app, app_pw, sql.SQL("NOBYPASSRLS"))):
            exists = c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            c.execute(sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE {} PASSWORD {}").format(
                sql.Identifier(role), extra, sql.Literal(pw)))
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone():
            c.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                sql.Identifier(db), sql.Identifier(owner)))
            print(f"[db_create] created database {db}")
        else:
            c.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(db), sql.Identifier(owner)))
    with conn(db) as c:
        for ext in ("pgcrypto", "vector"):
            c.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext)))
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db)))
        for r in (owner, app):
            c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(r)))
        c.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(owner)))
        c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    print("[db_create] roles/database/extensions ready")


if __name__ == "__main__":
    main()
