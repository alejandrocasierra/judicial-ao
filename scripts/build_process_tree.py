#!/usr/bin/env python3
"""Construye el árbol de carpetas del módulo Procesos desde el filesystem.

Para un expediente ya importado (documents/media con `cuaderno`):

  1. Crea en `case_folders` cada directorio del árbol (n niveles).
  2. Enlaza documents/media a su carpeta según su `cuaderno`
     (ej. "01PrimeraInstancia/0003Acumulado..." -> esa carpeta).
  3. Sube los XLSX del árbol: si el sha256 ya existe en documents (índices
     importados) sólo se enlaza a la carpeta; si no, se registra en case_files.
  4. NO sube PDFs ni videos (ya existen como registros del expediente).

Uso:

    python scripts/build_process_tree.py \
        --case-id <uuid> --folder /ruta/11001310302120180036100 \
        --org-id <uuid> --user-id <uuid> [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sqlalchemy import text

from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.services import audit, files
from app.services.storage import storage

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def get_or_create_folder(conn, *, org_id: str, case_id: str, user_id: str,
                         parent_id: str | None, name: str, cache: dict, dry_run: bool) -> str | None:
    """Devuelve el id de la carpeta (creándola si no existe). Cachea por (parent, name)."""
    key = (parent_id, name)
    if key in cache:
        return cache[key]
    existing = None
    if not (parent_id and str(parent_id).startswith("dry:")):
        existing = one(conn, """SELECT id FROM case_folders
                                WHERE case_id = :c AND parent_id IS NOT DISTINCT FROM :p AND lower(name) = lower(:n)""",
                       c=case_id, p=parent_id, n=name)
    if existing:
        cache[key] = str(existing["id"])
        return cache[key]
    if dry_run:
        cache[key] = f"dry:{parent_id}:{name}"
        return cache[key]
    row = one(conn, """INSERT INTO case_folders (organization_id, case_id, parent_id, name, created_by)
                       VALUES (:o,:c,:p,:n,:u) RETURNING id""",
              o=org_id, c=case_id, p=parent_id, n=name, u=user_id)
    cache[key] = str(row["id"])
    return cache[key]


def main() -> None:
    ap = argparse.ArgumentParser(description="Construye el árbol de carpetas de un proceso")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--folder", required=True, type=Path)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    UUID(args.case_id)
    UUID(args.org_id)
    UUID(args.user_id)
    root = args.folder.resolve()
    if not root.is_dir():
        raise SystemExit(f"folder not found: {root}")

    with tx(args.org_id, args.user_id) as conn:
        case = one(conn, "SELECT id, case_number, title FROM cases WHERE id = :c", c=args.case_id)
        if not case:
            raise AppError("CASE_NOT_FOUND", 404)

    folder_cache: dict = {}
    path_to_folder: dict[str, str | None] = {"": None}  # relpath (posix) -> folder_id (None = raíz)
    folders_created = 0
    xlsx_uploaded = 0
    xlsx_linked = 0
    docs_linked = 0
    media_linked = 0
    errors: list[dict] = []

    # 1-2. Carpetas (todos los niveles) + XLSX
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        rel_posix = rel.as_posix()
        if path.is_dir():
            parent_rel = rel.parent.as_posix() if rel.parent.as_posix() != "." else ""
            with tx(args.org_id, args.user_id) as conn:
                parent_id = path_to_folder.get(parent_rel)
                before = len(folder_cache)
                fid = get_or_create_folder(conn, org_id=args.org_id, case_id=args.case_id,
                                           user_id=args.user_id, parent_id=parent_id,
                                           name=path.name, cache=folder_cache, dry_run=args.dry_run)
                path_to_folder[rel_posix] = fid
                if len(folder_cache) > before:
                    folders_created += 1
        elif path.is_file() and path.suffix.lower() == ".xlsx":
            parent_rel = rel.parent.as_posix() if rel.parent.as_posix() != "." else ""
            folder_id = path_to_folder.get(parent_rel)
            try:
                sha = files.sha256_file(path)
                filename = files.sanitize_filename(path.name)
                with tx(args.org_id, args.user_id) as conn:
                    dup_doc = one(conn, "SELECT id FROM documents WHERE case_id = :c AND sha256 = :h",
                                  c=args.case_id, h=sha)
                    dup_file = one(conn, "SELECT id FROM case_files WHERE case_id = :c AND sha256 = :h",
                                   c=args.case_id, h=sha)
                    if dup_doc:
                        # El índice ya fue importado como documento: sólo enlazar a la carpeta.
                        if not args.dry_run:
                            conn.execute(
                                # documents es evidencia: sólo se actualiza folder_id (metadato organizativo)
                                text(
                                    "UPDATE documents SET folder_id = :f WHERE id = :d"),
                                {"f": folder_id, "d": str(dup_doc["id"])})
                        xlsx_linked += 1
                    elif dup_file:
                        if not args.dry_run:
                            conn.execute(
                                text(
                                    "UPDATE case_files SET folder_id = :f WHERE id = :d"),
                                {"f": folder_id, "d": str(dup_file["id"])})
                        xlsx_linked += 1
                    else:
                        if not args.dry_run:
                            files.scan_file(path)
                            uri = storage().put_file(f"cases/{args.case_id}/files/{sha}", path)
                            row = one(conn, """INSERT INTO case_files (organization_id, case_id, folder_id,
                                                   storage_uri, sha256, size_bytes, mime_type, filename, uploaded_by)
                                               VALUES (:o,:c,:f,:u,:h,:sz,:m,:fn,:by) RETURNING id""",
                                      o=args.org_id, c=args.case_id, f=folder_id, u=uri, h=sha,
                                      sz=path.stat().st_size, m=XLSX_MIME, fn=filename, by=args.user_id)
                            audit.record(conn, org_id=args.org_id, actor_id=args.user_id,
                                         action="file.imported", entity_type="file", entity_id=str(row["id"]),
                                         after={"sha256": sha, "filename": filename, "folder": parent_rel})
                        xlsx_uploaded += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": str(path), "error": str(exc)})

    # 3. Enlazar documents/media por su cuaderno -> carpeta
    with tx(args.org_id, args.user_id) as conn:
        for table, counter in (("documents", "docs"), ("media", "media")):
            for r in rows(conn, f"SELECT id, cuaderno FROM {table} WHERE case_id = :c AND cuaderno IS NOT NULL",
                          c=args.case_id):
                fid = path_to_folder.get(r["cuaderno"])
                if fid is None:
                    # cuaderno apunta a una carpeta que no está en el árbol físico: crearla por niveles
                    parts = r["cuaderno"].split("/")
                    parent_rel = ""
                    parent_id = None
                    for part in parts:
                        cur_rel = f"{parent_rel}/{part}".lstrip("/")
                        if cur_rel not in path_to_folder:
                            parent_id = get_or_create_folder(
                                conn, org_id=args.org_id, case_id=args.case_id, user_id=args.user_id,
                                parent_id=parent_id, name=part, cache=folder_cache, dry_run=args.dry_run)
                            path_to_folder[cur_rel] = parent_id
                            folders_created += 1
                        else:
                            parent_id = path_to_folder[cur_rel]
                        parent_rel = cur_rel
                    fid = parent_id
                if not args.dry_run and not str(fid).startswith("dry:"):
                    conn.execute(
                        text(
                            f"UPDATE {table} SET folder_id = :f WHERE id = :d"),
                        {"f": fid, "d": str(r["id"])})
                if counter == "docs":
                    docs_linked += 1
                else:
                    media_linked += 1
        if not args.dry_run:
            audit.record(conn, org_id=args.org_id, actor_id=args.user_id,
                         action="case.folder_tree_built", entity_type="case", entity_id=args.case_id,
                         after={"folders_created": folders_created, "xlsx_uploaded": xlsx_uploaded,
                                "xlsx_linked": xlsx_linked, "documents_linked": docs_linked,
                                "media_linked": media_linked, "source": str(root)})

    print(f"Árbol de carpetas {'(simulación)' if args.dry_run else 'construido'} para {case['case_number']} — {case['title']}")
    print(f"  carpetas creadas/localizadas: {folders_created}")
    print(f"  xlsx subidos a case_files:    {xlsx_uploaded}")
    print(f"  xlsx enlazados (ya existían): {xlsx_linked}")
    print(f"  documents enlazados:          {docs_linked}")
    print(f"  media enlazados:              {media_linked}")
    print(f"  errores:                      {len(errors)}")
    for e in errors[:20]:
        print(f"    - {e['path']}: {e['error']}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
