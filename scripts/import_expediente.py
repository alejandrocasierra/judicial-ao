#!/usr/bin/env python3
"""Importador masivo de un expediente desde filesystem (Fase 1).

Uso (CLI admin, no endpoint público):

    python scripts/import_expediente.py \
        --case-id <uuid> \
        --folder /ruta/al/expediente/11001310302120180036100 \
        --org-id <uuid> \
        --user-id <uuid> \
        [--dry-run]

El importador:
  - Lee los XLSX de índice (general + por cuaderno).
  - Recorre archivos del filesystem montado.
  - Empareja por cuaderno + número de índice (nunca solo por nombre).
  - Calcula sha256, deduplica por caso, sniffing de tipo real.
  - Guarda originales en storage write-once.
  - INSERT con linaje procesal en documents/media.
  - Registra el índice maestro como documento especial.
  - Deja audit trail de cada ingesta.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import UUID

# Permitir importar app.* desde apps/api
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sqlalchemy.engine import Connection

from app.core.db import one, tx
from app.core.errors import AppError
from app.services import audit, files
from app.services.index_xlsx import (
    IndexEntry,
    iter_expediente_indices,
    parse_cuaderno_index,
    parse_general_index,
)
from app.services.storage import key_from_uri, original_key, storage


@dataclass(frozen=True, slots=True)
class MatchedFile:
    """Archivo físico emparejado con una entrada de índice."""

    path: Path
    cuaderno: str
    indice_numero: int | None
    indice_nombre_original: str | None
    sub_orden: int
    entry: IndexEntry | None


@dataclass
class ImportResult:
    """Resultado acumulado del importador."""

    registered: int = 0
    duplicates: int = 0
    skipped: int = 0
    duplicate_paths: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def add(self, other: "ImportResult") -> None:
        self.registered += other.registered
        self.duplicates += other.duplicates
        self.skipped += other.skipped
        self.duplicate_paths.extend(other.duplicate_paths)
        self.errors.extend(other.errors)


def _extract_leading_number(name: str) -> int | None:
    m = re.match(r"^(\d+)", name)
    return int(m.group(1)) if m else None


def _candidate_index_number(relative: Path) -> int | None:
    """Extrae número candidato: primero de la carpeta contenedora, luego del archivo."""
    # Si está dentro de una subcarpeta, el nombre de esa carpeta puede ser el ítem índice
    if len(relative.parts) > 1:  # cuaderno/subcarpeta/.../archivo
        folder_name = relative.parts[-2]
        num = _extract_leading_number(folder_name)
        if num is not None:
            return num
    return _extract_leading_number(relative.name)


def _build_entry_map(entries: list[IndexEntry]) -> dict[int, list[IndexEntry]]:
    by_num: dict[int, list[IndexEntry]] = defaultdict(list)
    for e in entries:
        if e.indice_numero is not None:
            by_num[e.indice_numero].append(e)
    return by_num


def _match_files_in_cuaderno(
    cuaderno_dir: Path,
    cuaderno_name: str,
    entries: list[IndexEntry],
) -> list[MatchedFile]:
    """Empareja archivos físicos del cuaderno con entradas del índice.

    Reglas:
      1. Extrae número candidato del nombre (carpeta o archivo).
      2. Si coincide con una entrada del índice, usa esa entrada.
      3. Si varios archivos comparten el mismo número, se ordenan alfabéticamente
         y reciben sub_orden 0,1,2,...
      4. Si no coincide, se registra con indice_numero=None.
    """
    entry_map = _build_entry_map(entries)
    # Recolecta archivos físicos (excluye XLSX de índice)
    physical: list[Path] = []
    for p in sorted(cuaderno_dir.rglob("*")):
        if p.is_file() and p.name.lower() != "0000indiceexpedienteelectronico.xlsx":
            physical.append(p)

    # Agrupa por número candidato
    grouped: dict[int | None, list[Path]] = defaultdict(list)
    for p in physical:
        rel = p.relative_to(cuaderno_dir)
        grouped[_candidate_index_number(rel)].append(p)

    # Ordena alfabéticamente dentro de cada grupo para sub_orden estable
    for k in grouped:
        grouped[k].sort()

    matched: list[MatchedFile] = []
    # Procesa grupos con número conocido primero, luego sin número
    for num in sorted((k for k in grouped if k is not None), key=lambda x: x):
        for sub_orden, p in enumerate(grouped[num]):
            entry_list = entry_map.get(num, [])
            entry = (
                entry_list[sub_orden]
                if sub_orden < len(entry_list)
                else entry_list[-1] if entry_list else None
            )
            rel = p.relative_to(cuaderno_dir)
            matched.append(MatchedFile(
                path=p,
                cuaderno=cuaderno_name,
                indice_numero=num,
                indice_nombre_original=entry.nombre_original if entry else rel.name,
                sub_orden=sub_orden,
                entry=entry,
            ))
    for p in sorted(grouped[None]):
        rel = p.relative_to(cuaderno_dir)
        matched.append(MatchedFile(
            path=p,
            cuaderno=cuaderno_name,
            indice_numero=None,
            indice_nombre_original=rel.name,
            sub_orden=0,
            entry=None,
        ))
    return matched


def _orden_procesal(cuaderno: str, indice_numero: int | None, sub_orden: int) -> str:
    """Genera una clave sortable que refleja orden procesal global."""
    cuaderno_sort = cuaderno.replace(" ", "_").replace("/", "_")
    num_part = f"{indice_numero:08d}" if indice_numero is not None else "_none_"
    return f"{cuaderno_sort}/{num_part}/{sub_orden:04d}"


def _kind_and_mime(path: Path) -> tuple[str, str]:
    """Determina si es documento o media y su MIME real por magic bytes."""
    real = files.sniff_path(path)
    if real is None:
        # Fallback por extensión para tipos no soportados por sniff
        ext = files.extension(path.name)
        if ext in files.MIME:
            real = ext
        else:
            raise ValueError(f"tipo no soportado: {path.name}")
    mime = files.MIME[real]
    kind = "media" if mime.startswith(("video/", "audio/")) else "document"
    return kind, mime


def _insert_document(
    conn: Connection,
    *,
    org_id: str,
    case_id: str,
    user_id: str,
    uri: str,
    sha256: str,
    size: int,
    mime: str,
    filename: str,
    cuaderno: str,
    indice_numero: int | None,
    indice_nombre_original: str,
    sub_orden: int,
    orden_procesal: str,
    es_indice_maestro: bool,
    document_date: date | None,
    processing_status: str = "UPLOADED",
) -> dict:
    return one(conn, """
        INSERT INTO documents (
            organization_id, case_id, storage_uri, sha256, size_bytes, mime_type, filename,
            cuaderno, indice_numero, indice_nombre_original, sub_orden, orden_procesal,
            es_indice_maestro, document_date, uploaded_by, processing_status
        ) VALUES (
            :o, :c, :u, :h, :sz, :m, :f,
            :cuaderno, :indice_numero, :indice_nombre, :sub_orden, :orden,
            :es_indice, :doc_date, :by, :processing_status
        ) RETURNING id, sha256, size_bytes, mime_type, filename, processing_status, created_at
    """,
        o=org_id, c=case_id, u=uri, h=sha256, sz=size, m=mime, f=filename,
        cuaderno=cuaderno, indice_numero=indice_numero, indice_nombre=indice_nombre_original,
        sub_orden=sub_orden, orden=orden_procesal, es_indice=es_indice_maestro,
        doc_date=document_date, by=user_id, processing_status=processing_status)


def _insert_media(
    conn: Connection,
    *,
    org_id: str,
    case_id: str,
    user_id: str,
    uri: str,
    sha256: str,
    size: int,
    mime: str,
    filename: str,
    title: str | None,
    cuaderno: str,
    indice_numero: int | None,
    indice_nombre_original: str,
    sub_orden: int,
    orden_procesal: str,
    es_indice_maestro: bool,
) -> dict:
    return one(conn, """
        INSERT INTO media (
            organization_id, case_id, storage_uri, sha256, size_bytes, mime_type, filename, title,
            media_type, cuaderno, indice_numero, indice_nombre_original, sub_orden, orden_procesal,
            es_indice_maestro, uploaded_by, processing_status
        ) VALUES (
            :o, :c, :u, :h, :sz, :m, :f, :t,
            :mt, :cuaderno, :indice_numero, :indice_nombre, :sub_orden, :orden,
            :es_indice, :by, 'UPLOADED'
        ) RETURNING id, sha256, size_bytes, mime_type, filename, media_type, processing_status, created_at
    """,
        o=org_id, c=case_id, u=uri, h=sha256, sz=size, m=mime, f=filename, t=title,
        mt="video" if mime.startswith("video") else "audio",
        cuaderno=cuaderno, indice_numero=indice_numero, indice_nombre=indice_nombre_original,
        sub_orden=sub_orden, orden=orden_procesal, es_indice=es_indice_maestro, by=user_id)


def _register_file(
    conn: Connection,
    matched: MatchedFile,
    *,
    org_id: str,
    case_id: str,
    user_id: str,
    dry_run: bool,
) -> tuple[str, dict | None]:
    """Registra un archivo físico. Devuelve ('registered'|'duplicate'|'error', row|None)."""
    path = matched.path
    filename = files.sanitize_filename(path.name)
    sha256 = files.sha256_file(path)

    # Dedup por caso
    dup_doc = one(conn, "SELECT id FROM documents WHERE case_id = :c AND sha256 = :h", c=case_id, h=sha256)
    dup_media = one(conn, "SELECT id FROM media WHERE case_id = :c AND sha256 = :h", c=case_id, h=sha256)
    if dup_doc or dup_media:
        return "duplicate", {"path": str(path), "sha256": sha256}

    kind, mime = _kind_and_mime(path)
    size = path.stat().st_size
    orden = _orden_procesal(matched.cuaderno, matched.indice_numero, matched.sub_orden)
    doc_date = matched.entry.fecha_creacion if matched.entry else None

    if dry_run:
        return "registered", {
            "kind": kind, "sha256": sha256, "size": size, "mime": mime,
            "filename": filename, "cuaderno": matched.cuaderno,
            "indice_numero": matched.indice_numero,
            "indice_nombre_original": matched.indice_nombre_original,
            "sub_orden": matched.sub_orden, "orden_procesal": orden,
        }

    # Rechaza malware antes de tocar storage
    files.scan_file(path)

    key = original_key(case_id, sha256, kind)
    uri = storage().put_file(key, path)
    # Re-verifica integridad del objeto almacenado contra el hash local
    stored_sha = storage().sha256(key_from_uri(uri))
    if stored_sha != sha256:
        raise RuntimeError("integrity check failed after copy")

    if kind == "document":
        row = _insert_document(
            conn, org_id=org_id, case_id=case_id, user_id=user_id, uri=uri, sha256=sha256,
            size=size, mime=mime, filename=filename, cuaderno=matched.cuaderno,
            indice_numero=matched.indice_numero,
            indice_nombre_original=matched.indice_nombre_original or filename,
            sub_orden=matched.sub_orden, orden_procesal=orden,
            es_indice_maestro=False, document_date=doc_date)
    else:
        row = _insert_media(
            conn, org_id=org_id, case_id=case_id, user_id=user_id, uri=uri, sha256=sha256,
            size=size, mime=mime, filename=filename, title=matched.indice_nombre_original,
            cuaderno=matched.cuaderno, indice_numero=matched.indice_numero,
            indice_nombre_original=matched.indice_nombre_original or filename,
            sub_orden=matched.sub_orden, orden_procesal=orden,
            es_indice_maestro=False)

    audit.record(conn, org_id=org_id, actor_id=user_id, action=f"{kind}.imported",
                 entity_type=kind, entity_id=str(row["id"]),
                 after={"sha256": sha256, "size": size, "cuaderno": matched.cuaderno,
                        "indice_numero": matched.indice_numero, "orden_procesal": orden})
    return "registered", row


def _register_index_file(
    conn: Connection,
    *,
    path: Path,
    cuaderno: str | None,
    org_id: str,
    case_id: str,
    user_id: str,
    dry_run: bool,
) -> tuple[str, dict | None]:
    """Registra un XLSX de índice como documento especial del caso."""
    filename = files.sanitize_filename(path.name)
    sha256 = files.sha256_file(path)
    dup = one(conn, "SELECT id FROM documents WHERE case_id = :c AND sha256 = :h", c=case_id, h=sha256)
    if dup:
        return "duplicate", None
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    size = path.stat().st_size
    if dry_run:
        return "registered", {"kind": "document", "sha256": sha256, "filename": filename, "es_indice_maestro": True}
    files.scan_file(path)
    key = original_key(case_id, sha256, "document")
    uri = storage().put_file(key, path)
    orden = _orden_procesal(cuaderno or "00GENERAL", None, 0)
    row = _insert_document(
        conn, org_id=org_id, case_id=case_id, user_id=user_id, uri=uri, sha256=sha256,
        size=size, mime=mime, filename=filename, cuaderno=cuaderno,
        indice_numero=None, indice_nombre_original=filename, sub_orden=0,
        orden_procesal=orden, es_indice_maestro=True,
        document_date=None, processing_status="INDEXED")
    action = "document.master_index_imported" if cuaderno is None else "document.cuaderno_index_imported"
    audit.record(conn, org_id=org_id, actor_id=user_id, action=action,
                 entity_type="document", entity_id=str(row["id"]),
                 after={"sha256": sha256, "size": size, "filename": filename, "cuaderno": cuaderno})
    return "registered", row


def import_cuaderno(
    cuaderno_dir: Path,
    cuaderno_name: str,
    *,
    org_id: str,
    case_id: str,
    user_id: str,
    dry_run: bool,
) -> ImportResult:
    """Importa todos los archivos de un cuaderno, incluyendo su XLSX de índice."""
    index_file = cuaderno_dir / "0000IndiceExpedienteElectronico.xlsx"
    if not index_file.exists():
        entries: list[IndexEntry] = []
    else:
        entries = parse_cuaderno_index(index_file, cuaderno_name).entries

    result = ImportResult()

    # Registra el XLSX de índice del cuaderno como documento especial
    if index_file.exists():
        with tx(org_id, user_id) as conn:
            try:
                status, _ = _register_index_file(conn, path=index_file, cuaderno=cuaderno_name,
                                                 org_id=org_id, case_id=case_id, user_id=user_id,
                                                 dry_run=dry_run)
                if status == "registered":
                    result.registered += 1
                elif status == "duplicate":
                    result.duplicates += 1
            except files.MalwareFound:
                raise
            except Exception as exc:  # noqa: BLE001
                result.errors.append({"path": str(index_file), "error": str(exc)})

    matched = _match_files_in_cuaderno(cuaderno_dir, cuaderno_name, entries)
    with tx(org_id, user_id) as conn:
        for m in matched:
            try:
                status, _ = _register_file(conn, m, org_id=org_id, case_id=case_id,
                                           user_id=user_id, dry_run=dry_run)
                if status == "registered":
                    result.registered += 1
                elif status == "duplicate":
                    result.duplicates += 1
                    result.duplicate_paths.append(str(m.path))
            except files.MalwareFound:
                raise
            except Exception as exc:  # noqa: BLE001
                result.errors.append({"path": str(m.path), "error": str(exc)})
    return result


def import_expediente(
    *,
    folder: Path,
    case_id: str,
    org_id: str,
    user_id: str,
    dry_run: bool = False,
) -> ImportResult:
    """Importa una carpeta de expediente completa en un caso existente."""
    folder = folder.resolve()
    if not folder.is_dir():
        raise ValueError(f"folder not found: {folder}")

    # Validar caso y pertenencia a la org
    with tx(org_id, user_id) as conn:
        case = one(conn, "SELECT id, organization_id, status, case_number FROM cases WHERE id = :c", c=case_id)
        if not case:
            raise AppError("CASE_NOT_FOUND", 404)
        if str(case["organization_id"]) != org_id:
            raise AppError("ORGANIZATION_MISMATCH", 403)
        if case["status"] == "ARCHIVED":
            raise AppError("INVALID_STATE_TRANSITION", 409)

    total = ImportResult()
    indexed: set[str] = set()

    # Índice general: validar radicación y registrar
    general_index = folder / "0000IndiceExpedienteGeneral.xlsx"
    if general_index.exists():
        general_idx = parse_general_index(general_index)
        if general_idx.metadata.radicacion and general_idx.metadata.radicacion != case["case_number"]:
            raise AppError("CASE_NUMBER_MISMATCH", 400)
        with tx(org_id, user_id) as conn:
            try:
                status, _ = _register_index_file(conn, path=general_index, cuaderno=None, org_id=org_id,
                                                 case_id=case_id, user_id=user_id, dry_run=dry_run)
                if status == "registered":
                    total.registered += 1
                elif status == "duplicate":
                    total.duplicates += 1
            except files.MalwareFound:
                raise
            except Exception as exc:  # noqa: BLE001
                total.errors.append({"path": str(general_index), "error": str(exc)})

    for idx in iter_expediente_indices(folder):
        cuaderno_name = idx.cuaderno
        cuaderno_dir = folder / cuaderno_name
        result = import_cuaderno(
            cuaderno_dir, cuaderno_name,
            org_id=org_id, case_id=case_id, user_id=user_id, dry_run=dry_run)
        total.add(result)
        indexed.add(cuaderno_name)

    # Carpetas sin XLSX de índice (ej: 02SegundaInstancia/C001ApelacionAuto)
    for instancia in sorted(folder.iterdir()):
        if not instancia.is_dir():
            continue
        for cuaderno_dir in sorted(instancia.iterdir()):
            if not cuaderno_dir.is_dir():
                continue
            cuaderno_name = f"{instancia.name}/{cuaderno_dir.name}"
            if cuaderno_name in indexed:
                continue
            result = import_cuaderno(
                cuaderno_dir, cuaderno_name,
                org_id=org_id, case_id=case_id, user_id=user_id, dry_run=dry_run)
            total.add(result)

    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="Importador masivo de expediente judicial")
    ap.add_argument("--case-id", required=True, help="UUID del caso destino")
    ap.add_argument("--folder", required=True, type=Path, help="Ruta a la carpeta del expediente")
    ap.add_argument("--org-id", required=True, help="UUID de la organización")
    ap.add_argument("--user-id", required=True, help="UUID del usuario admin que importa")
    ap.add_argument("--dry-run", action="store_true", help="No escribe en BD ni storage")
    args = ap.parse_args()

    # Validación temprana de UUIDs
    UUID(args.case_id)
    UUID(args.org_id)
    UUID(args.user_id)

    result = import_expediente(
        folder=args.folder,
        case_id=args.case_id,
        org_id=args.org_id,
        user_id=args.user_id,
        dry_run=args.dry_run,
    )

    print(f"Importación {'(simulación)' if args.dry_run else 'completada'}:")
    print(f"  registrados: {result.registered}")
    print(f"  duplicados:  {result.duplicates}")
    if result.duplicate_paths:
        print("  rutas duplicadas:")
        for dp in result.duplicate_paths:
            print(f"    - {dp}")
    print(f"  errores:     {len(result.errors)}")
    if result.errors:
        for e in result.errors[:20]:
            print(f"    - {e['path']}: {e['error']}")
        if len(result.errors) > 20:
            print(f"    ... y {len(result.errors) - 20} errores más")
    sys.exit(1 if result.errors else 0)


if __name__ == "__main__":
    main()
