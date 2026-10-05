#!/usr/bin/env python3
"""Exporta UN expediente (filas de la BD + archivos locales) a una carpeta portátil.

    python scripts/export_case.py --case-id <uuid> --org-id <uuid> [--out casos/<id>] [--no-files]

No modifica la base de datos. El resultado contiene datos reales: muévelo por un canal privado.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import case_transfer as ct


def _storage_keys(cur, case_id: str) -> list[tuple[str, str]]:
    """Devuelve [(uri, key)] de los originales/imágenes del caso."""
    out: list[tuple[str, str]] = []
    cur.execute("SELECT storage_uri FROM documents WHERE case_id = %s AND storage_uri <> ''", (case_id,))
    out += [(r[0], "document") for r in cur.fetchall()]
    cur.execute("SELECT storage_uri FROM media WHERE case_id = %s AND storage_uri <> ''", (case_id,))
    out += [(r[0], "media") for r in cur.fetchall()]
    cur.execute("""SELECT p.image_uri FROM document_pages p JOIN documents d ON d.id = p.document_id
        WHERE d.case_id = %s AND p.image_uri <> ''""", (case_id,))
    out += [(r[0], "page") for r in cur.fetchall()]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-files", action="store_true", help="omitir los archivos de storage")
    a = ap.parse_args()

    out = Path(a.out) if a.out else Path("casos") / a.case_id
    (out / "tables").mkdir(parents=True, exist_ok=True)

    conn = ct.connect(a.org_id)
    manifest = {"org_id": a.org_id, "case_id": a.case_id, "tables": {}, "storage": {}}
    try:
        cur = conn.cursor()
        cur.execute("SELECT case_number, title FROM cases WHERE id = %s", (a.case_id,))
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"ERROR: el caso {a.case_id} no existe en la organización {a.org_id}")
        manifest["case_number"], manifest["title"] = row[0], row[1]

        print("==> Exportando tablas")
        for table in ct.IMPORT_ORDER:
            where = ct.EXPORT_WHERE.get(table)
            if not where:
                continue
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
                        (table,))
            if not cur.fetchone():
                continue
            cols = ct.insertable_columns(cur, table)
            if not cols:
                continue
            file = out / "tables" / f"{table}.csv"
            ct.export_table(cur, table, where, cols, file, a.case_id)
            manifest["tables"][table] = cols
            print(f"    {table}")

        if not a.no_files:
            print("==> Copiando originales (storage)")
            from app.services.storage import key_from_uri, storage
            (out / "storage").mkdir(exist_ok=True)
            for uri, kind in _storage_keys(cur, a.case_id):
                try:
                    key = key_from_uri(uri)
                    data = storage().get(key)
                except Exception as e:  # noqa: BLE001
                    print(f"    ! no se pudo leer {uri}: {e}")
                    continue
                dest = out / "storage" / key
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                manifest["storage"][key] = kind
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        conn.close()

    print(f"\nExportado en: {out}")
    print("Muévelo en privado y en el destino corre:")
    print(f"  python scripts/import_case.py --in {out}")


if __name__ == "__main__":
    main()
