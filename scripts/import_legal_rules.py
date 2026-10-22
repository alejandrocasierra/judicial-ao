"""Importa normas oficiales a `legal_rules` sin depender de memoria del modelo.

Cada archivo JSON debe ser una lista de objetos:
  {
    "jurisdiction": "co",
    "source": "Codigo Civil",
    "identifier": "123",
    "title": "Articulo 123",
    "text": "texto oficial",
    "version_date": "2012-07-12",
    "official_url": "https://...",
    "checksum": "sha256 del texto"
  }

Uso:
  python scripts/import_legal_rules.py --dir data/legal_rules/co --organization-id <uuid>
  python scripts/import_legal_rules.py --dir data/legal_rules/co --all --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))
import envload  # noqa: E402
envload.load(str(_ROOT / ".env"))

from sqlalchemy import create_engine, text  # noqa: E402


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variable de entorno requerida: {name}")
    return value


def _owner_engine():
    url = (f"postgresql+psycopg://{_env('POSTGRES_SUPERUSER')}:{quote_plus(_env('POSTGRES_SUPERUSER_PASSWORD'))}"
           f"@{_env('POSTGRES_HOST')}:{_env('POSTGRES_PORT')}/{_env('POSTGRES_DB')}")
    return create_engine(url)


def _orgs(engine):
    with engine.connect() as conn:
        return [str(r[0]) for r in conn.execute(text("SELECT id FROM organizations ORDER BY name"))]


def _rules(directory: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("rules") or [data]
        if not isinstance(data, list):
            raise ValueError(f"{path}: se esperaba una lista o {{rules: []}}")
        for item in data:
            if not all(item.get(k) for k in ("jurisdiction", "source", "identifier")):
                raise ValueError(f"{path}: faltan jurisdiction/source/identifier")
            text_value = item.get("text") or ""
            checksum = item.get("checksum")
            if checksum and text_value:
                actual = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
                if actual != checksum:
                    raise ValueError(f"{path}: checksum no coincide para {item['identifier']}")
            out.append(item)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Importar normas oficiales")
    parser.add_argument("--dir", default="data/legal_rules/co")
    parser.add_argument("--organization-id")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    directory = Path(args.dir)
    if not directory.exists():
        raise SystemExit(f"No existe {directory}")
    rules = _rules(directory)
    if not rules:
        print("No hay normas JSON para importar. El gate bloqueara conclusiones juridicas no verificadas.")
        return
    engine = _owner_engine()
    orgs = _orgs(engine)
    if args.organization_id:
        orgs = [args.organization_id]
    if not orgs:
        raise SystemExit("No hay organizaciones")
    total = 0
    for org in orgs:
        for rule in rules:
            if args.dry_run:
                print(f"[dry-run] {org} {rule['jurisdiction']} {rule['source']} {rule['identifier']}")
                total += 1
                continue
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO legal_rules
                      (organization_id, jurisdiction, source, identifier, title, text,
                       version_date, official_url, checksum, verified_at, is_active, metadata)
                    VALUES (:o,:j,:s,:i,:t,:x,
                            CASE WHEN :vd = '' THEN NULL ELSE CAST(:vd AS date) END,
                            :url,:ch,now(),true,CAST(:m AS jsonb))
                    ON CONFLICT (organization_id, jurisdiction, source, identifier, version_date)
                    DO UPDATE SET title=EXCLUDED.title, text=EXCLUDED.text,
                      official_url=EXCLUDED.official_url, checksum=EXCLUDED.checksum,
                      verified_at=now(), is_active=true, metadata=EXCLUDED.metadata
                """), {"o": org, "j": rule["jurisdiction"], "s": rule["source"],
                       "i": rule["identifier"], "t": rule.get("title"),
                       "x": rule.get("text") or "", "vd": rule.get("version_date") or "",
                       "url": rule.get("official_url"), "ch": rule.get("checksum"),
                       "m": json.dumps(rule.get("metadata") or {}, ensure_ascii=False)})
            total += 1
    print(f"{'Simuladas' if args.dry_run else 'Importadas'}: {total} normas")


if __name__ == "__main__":
    main()
