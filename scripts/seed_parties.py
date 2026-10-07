# -*- coding: utf-8 -*-
"""Siembra las PARTES y roles del proceso (idempotente).

Se ejecuta DENTRO del contenedor `api` (tiene las credenciales y el código de la app).

Localiza el caso por radicado (`CASE_NUMBER`) o id (`CASE_ID`) usando el superusuario
(bypassa RLS), y luego inserta roles/partes con el código de la app (RLS con la org del caso).

Variables (opcionales):
  CASE_NUMBER   radicado del proceso (por defecto 11001310302120180036100)
  CASE_ID       uuid del caso (tiene prioridad sobre CASE_NUMBER)
  ORG_ID        restringe la búsqueda a una organización

Uso:
  docker compose exec -T api python /srv/scripts/seed_parties.py
"""
from __future__ import annotations

import os
import sys
from urllib.parse import quote

from sqlalchemy import create_engine, text

from app.core.db import rows, tx
from app.services import party_extraction
from app.services import party_roles as pr

CASE_NUMBER = os.environ.get("CASE_NUMBER", "11001310302120180036100").strip()
CASE_ID = os.environ.get("CASE_ID", "").strip()
ORG_ID = os.environ.get("ORG_ID", "").strip()

# Roles extra además de los por defecto (Demandante, Demandado, Apoderado, …).
EXTRA_ROLES = [("magistrado", "Magistrado", 5)]

# Partes identificadas (OCR modo Document AI + ASR). Dedup por nombre en auto_upsert.
PARTIES = [
    {"name": "José Rueda Avellaneda", "role": "claimant", "entity_type": "person",
     "aliases": ["JOSE RUEDA AVELLANEDA", "JOSE RUDA AVELLANEDA", "RUEDA AVELLANEDA"]},
    {"name": "Inverfast S.A.S.", "role": "claimant", "entity_type": "organization",
     "aliases": ["INVERFAST S.A.S", "INVERFAST SAS"]},
    {"name": "Aparicio Cañón Herrera", "role": "claimant", "entity_type": "person", "aliases": []},
    {"name": "Juan Carlos Garzón Gutiérrez", "role": "defendant", "entity_type": "person",
     "aliases": ["JUAN CARLOS GARZON GUTIERREZ"]},
    {"name": "Carlos Alfonso Garzón Gutiérrez", "role": "defendant", "entity_type": "person",
     "aliases": ["CARLOS ALFONSO GARZON GUTIEREZ", "CARLOS ALFONSO GARZON GUTIERREZ"]},
    {"name": "Janneth Quijano Morant", "role": "attorney", "entity_type": "person",
     "aliases": ["Janneth Quijano Morent"]},
    {"name": "Paola Ibáñez", "role": "attorney", "entity_type": "person", "aliases": ["Paola Ibanez"]},
    {"name": "Carlos Alfonso Gómez Garcés", "role": "attorney", "entity_type": "person",
     "aliases": ["CARLOS ALFONSO GOMEZ GARCES"]},
    {"name": "Jorge Humberto Rojas Melo", "role": "representative", "entity_type": "person", "aliases": []},
    {"name": "Alba Lucy Cock Álvarez", "role": "judge", "entity_type": "person", "aliases": []},
    {"name": "Sandra Cecilia Rodríguez Eslava", "role": "magistrado", "entity_type": "person", "aliases": []},
    {"name": "Jaime Londoño Salazar", "role": "magistrado", "entity_type": "person",
     "aliases": ["Jaime Londono Salazar"]},
    {"name": "Aroldo Wilson Quiroz Monsalvo", "role": "magistrado", "entity_type": "person", "aliases": []},
    {"name": "José Alfonso Isaza Dávila", "role": "magistrado", "entity_type": "person", "aliases": []},
    {"name": "María Patricia Cruz Miranda", "role": "magistrado", "entity_type": "person", "aliases": []},
]


def _super_engine():
    host = os.environ["POSTGRES_HOST"]
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ["POSTGRES_DB"]
    user = os.environ["POSTGRES_SUPERUSER"]
    pwd = os.environ["POSTGRES_SUPERUSER_PASSWORD"]
    url = f"postgresql+psycopg://{quote(user)}:{quote(pwd)}@{host}:{port}/{db}"
    return create_engine(url, future=True)


def find_case() -> dict | None:
    """Busca el caso con el superusuario (bypassa RLS)."""
    with _super_engine().connect() as c:
        if CASE_ID:
            r = c.execute(text("SELECT id, organization_id, case_number FROM cases WHERE id = :i"),
                          {"i": CASE_ID}).mappings().first()
            if r:
                return dict(r)
        params: dict = {"n": CASE_NUMBER}
        q = "SELECT id, organization_id, case_number FROM cases WHERE case_number = :n"
        if ORG_ID:
            q += " AND organization_id = :o"
            params["o"] = ORG_ID
        r = c.execute(text(q + " ORDER BY created_at DESC LIMIT 1"), params).mappings().first()
    return dict(r) if r else None


def main() -> int:
    case = find_case()
    if not case:
        print(f"ERROR: no se encontró el caso (CASE_NUMBER={CASE_NUMBER!r}, CASE_ID={CASE_ID!r})")
        return 1
    org, cid = str(case["organization_id"]), str(case["id"])

    with tx(org) as c:
        pr.ensure_defaults(c, org, cid)
        for code, label, order in EXTRA_ROLES:
            if not rows(c, "SELECT 1 FROM party_roles WHERE case_id = :c AND code = :k", c=cid, k=code):
                c.execute(text("""INSERT INTO party_roles
                        (organization_id, case_id, code, label, is_system, sort_order)
                        VALUES (:o, :c, :k, :l, false, :s)"""),
                          {"o": org, "c": cid, "k": code, "l": label, "s": order})
        res = party_extraction.auto_upsert(c, org, cid, PARTIES)
        total = rows(c, "SELECT count(*) AS n FROM parties WHERE case_id = :c", c=cid)[0]["n"]
        roles_n = rows(c, "SELECT count(*) AS n FROM party_roles WHERE case_id = :c", c=cid)[0]["n"]

    print(f"caso {case['case_number']}  id={cid}")
    print(f"  roles: {roles_n}")
    print(f"  partes creadas: {len(res['created'])}  omitidas (ya existían): {res['skipped']}")
    print(f"  total partes: {total}")
    for p in res["created"]:
        print(f"    + [{p['role']}] {p['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
