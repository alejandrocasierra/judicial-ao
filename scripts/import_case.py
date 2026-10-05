#!/usr/bin/env python3
"""Importa un expediente exportado con export_case.py a ESTA instancia.

    python scripts/import_case.py --in casos/<id> [--org-id <uuid-destino>] [--no-files]

- Sin `--org-id`: usa la organización del manifiesto (mismo id).
- Con `--org-id` de OTRA organización existente: **remapea** el caso a esa organización,
  reinserta los usuarios referenciados (remap de org) y, si un email ya existe en el
  destino, reutiliza ese usuario (remapea las referencias). Así el proceso aparece en tu
  instancia de producción sin reemplazar toda la BD.

Usa el superusuario (como pg_restore). Pensado para un destino que aún no tiene el caso.
"""
from __future__ import annotations

import argparse
import csv
import json
import tempfile
from pathlib import Path

import case_transfer as ct


def _write_temp(text: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w",
                                      encoding="utf-8", errors="surrogateescape", newline="")
    tmp.write(text)
    tmp.close()
    return Path(tmp.name)


def _rewrite(file: Path, repl: dict[str, str]) -> Path:
    if not repl:
        return file
    data = file.read_text(encoding="utf-8", errors="surrogateescape")
    for a, b in repl.items():
        if a and a != b:
            data = data.replace(a, b)
    return _write_temp(data)


def _blank_columns(file: Path, names: list[str]) -> Path:
    """Pone a NULL (vacío) las columnas indicadas (p. ej. agent_id/model_id al cambiar de org)."""
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w",
                                      encoding="utf-8", errors="surrogateescape", newline="")
    with open(file, newline="", encoding="utf-8", errors="surrogateescape") as fh, out:
        r, w = csv.reader(fh), csv.writer(out)
        header = next(r)
        w.writerow(header)
        idxs = [header.index(n) for n in names if n in header]
        for row in r:
            for i in idxs:
                row[i] = "\\N"
            w.writerow(row)
    out.close()
    return Path(out.name)


def _filter_users(file: Path, skip_ids: set[str]) -> Path:
    """Quita de users.csv los usuarios que ya existen (por id) en el destino."""
    if not skip_ids:
        return file
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w",
                                      encoding="utf-8", errors="surrogateescape", newline="")
    with open(file, newline="", encoding="utf-8", errors="surrogateescape") as fh, out:
        r, w = csv.reader(fh), csv.writer(out)
        header = next(r)
        w.writerow(header)
        idx = header.index("id")
        for row in r:
            if row[idx] not in skip_ids:
                w.writerow(row)
    out.close()
    return Path(out.name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--org-id", default=None, help="organización destino (por defecto, la del manifiesto)")
    ap.add_argument("--no-files", action="store_true")
    a = ap.parse_args()

    src = Path(a.src)
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    src_org = manifest["org_id"]
    dst_org = a.org_id or src_org
    same = src_org == dst_org
    print(f"==> Importando caso {manifest.get('case_number') or manifest['case_id']}")
    print(f"    org origen={src_org}  destino={dst_org}")

    conn = ct.connect(dst_org)
    total = 0
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM ops_list_organizations() WHERE id = %s", (dst_org,))
        dst_exists = cur.fetchone() is not None
        if not same and not dst_exists:
            raise SystemExit(f"ERROR: la organización destino {dst_org} no existe.")
        cur.execute("SELECT 1 FROM cases WHERE id = %s", (manifest["case_id"],))
        if cur.fetchone() is not None:
            raise SystemExit(f"ERROR: el caso {manifest['case_id']} ya existe. Bórralo antes o usa la migración de instancia.")

        # Remapeos: org origen -> destino, y usuarios que ya existen (por email) -> su id.
        repl: dict[str, str] = {} if same else {src_org: dst_org}
        skip_user_ids: set[str] = set()
        users_file = src / "tables" / "users.csv"
        if users_file.exists():
            with open(users_file, newline="", encoding="utf-8", errors="surrogateescape") as fh:
                r = csv.reader(fh)
                header = next(r)
                i_id, i_email = header.index("id"), header.index("email")
                for row in r:
                    uid, email = row[i_id], row[i_email]
                    cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                    ex = cur.fetchone()
                    if ex:
                        repl[uid] = str(ex[0])
                        skip_user_ids.add(uid)
        if skip_user_ids:
            print(f"    usuarios existentes reutilizados: {len(skip_user_ids)}")

        skip_org_row = dst_exists
        skip_users = same and dst_exists

        for table in ct.IMPORT_ORDER:
            if table == "organizations" and skip_org_row:
                continue
            if table == "users" and skip_users:
                continue
            cols = manifest["tables"].get(table)
            file = src / "tables" / f"{table}.csv"
            if not cols or not file.exists():
                continue
            tmp_filtered = _filter_users(file, skip_user_ids) if table == "users" else file
            if table == "chat_sessions" and not same:
                tmp_filtered = _blank_columns(tmp_filtered, ["agent_id", "model_id"])
            use_file = _rewrite(tmp_filtered, repl)
            try:
                n = ct.import_table(cur, table, cols, use_file)
            finally:
                for p in {tmp_filtered, use_file}:
                    if p != file and p.exists():
                        p.unlink(missing_ok=True)
            total += n
            print(f"    {table}: {n} filas")
        conn.commit()
    finally:
        conn.close()

    if not a.no_files:
        from app.services.storage import storage
        st = storage()
        copied = 0
        for key in (manifest.get("storage") or {}):
            f = src / "storage" / key
            if f.exists():
                st.put(key, f.read_bytes())
                copied += 1
        print(f"==> Archivos restaurados: {copied}")

    print(f"\nListo. Filas insertadas: {total}. Abre el proceso en el dashboard.")


if __name__ == "__main__":
    main()
