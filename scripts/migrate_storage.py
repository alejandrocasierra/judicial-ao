"""Migra los archivos locales (var/storage) al almacenamiento de objetos (GCS/S3).

Diseño sin riesgo:
- Sube cada archivo conservando su CLAVE LÓGICA (ruta relativa dentro de
  var/storage), aplicando la carpeta S3_PREFIX. Ejemplo local:
      var/storage/cases/<caso>/originals/<sha>
  ->  gs://<bucket>/<prefijo>/cases/<caso>/originals/<sha>
- NO modifica la base de datos, ni pgvector, ni el grafo: las referencias
  (`local://cases/...`) siguen resolviendo porque el sistema usa la clave lógica
  después del `://`. Al cambiar STORAGE_BACKEND, el mismo archivo se lee del bucket.
- Idempotente y reanudable: omite lo que ya existe (salvo --force).

Uso:
  python scripts/migrate_storage.py --check                 # valida credenciales/bucket
  python scripts/migrate_storage.py --dry-run               # lista sin subir
  python scripts/migrate_storage.py --backend gcs           # migra a Google Cloud Storage
  python scripts/migrate_storage.py --backend gcs --verify  # sube y comprueba sha256
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "api"))


def logical_key_from_path(root: Path, path: Path) -> str:
    """Clave lógica = ruta relativa POSIX dentro del storage local."""
    return path.relative_to(root).as_posix()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _dest(backend: str):
    from app.services.storage import GcsStorage, S3Storage
    return GcsStorage() if backend == "gcs" else S3Storage()


def main() -> None:
    ap = argparse.ArgumentParser(description="Migra var/storage al bucket (GCS/S3).")
    ap.add_argument("--backend", choices=["gcs", "s3"], default=os.environ.get("STORAGE_BACKEND", "gcs"))
    ap.add_argument("--dry-run", action="store_true", help="no sube nada; solo lista")
    ap.add_argument("--force", action="store_true", help="re-sube aunque ya exista")
    ap.add_argument("--verify", action="store_true", help="descarga y compara sha256 tras subir")
    ap.add_argument("--check", action="store_true", help="prueba de credenciales y bucket")
    a = ap.parse_args()

    import envload
    envload.load(os.environ.get("ENV_FILE", str(ROOT / ".env")), override=True)

    from app.core.config import get_settings
    from app.services.storage import normalize_prefix

    s = get_settings()
    backend = a.backend if a.backend in ("gcs", "s3") else "gcs"
    root = s.path(s.STORAGE_LOCAL_ROOT)
    carpeta = normalize_prefix(s.S3_PREFIX).rstrip("/") or "(raíz)"
    scheme = "gs" if backend == "gcs" else "s3"
    print(f"origen : {root}")
    print(f"destino: {backend} · bucket={s.S3_BUCKET} · carpeta='{carpeta}'")

    dest = _dest(backend)

    if a.check:
        probe = "_healthcheck.txt"
        payload = b"judicial-ai storage check"
        dest.put(probe, payload)
        got = dest.get(probe)
        dest.delete_prefix(probe)
        assert got == payload, "el objeto de prueba no coincide"
        print(f"[OK] check OK: escritura/lectura/borrado en {scheme}://{s.S3_BUCKET}/{carpeta}")
        return

    if not root.exists():
        print(f"[AVISO]  El storage local no existe todavía: {root}")
        return

    files = [p for p in root.rglob("*") if p.is_file()]
    total_bytes = sum(p.stat().st_size for p in files)
    print(f"archivos locales: {len(files)} · {total_bytes / 1048576:.1f} MB\n")

    uploaded = skipped = failed = 0
    bytes_up = 0
    for i, p in enumerate(files, 1):
        key = logical_key_from_path(root, p)
        if a.dry_run:
            print(f"[dry-run] {key} -> {scheme}://{s.S3_BUCKET}/{carpeta}/{key} ({p.stat().st_size} bytes)")
            continue
        try:
            if not a.force and dest.size(key) == p.stat().st_size:
                skipped += 1
                continue
            dest.put_file(key, p)
            if a.verify:
                if dest.sha256(key) != _sha256_file(p):
                    raise RuntimeError("sha256 no coincide tras la subida")
            uploaded += 1
            bytes_up += p.stat().st_size
            if i % 25 == 0 or i == len(files):
                print(f"  {i}/{len(files)} · subidos={uploaded} omitidos={skipped}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [ERROR] {key}: {exc}")

    if a.dry_run:
        print("\n[dry-run] no se subió nada.")
        return
    print(f"\n[OK] listo: subidos={uploaded} · omitidos={skipped} · fallidos={failed} · {bytes_up / 1048576:.1f} MB")
    if failed == 0 and uploaded + skipped > 0:
        print("Ahora pon en .env: STORAGE_BACKEND=gcs y reinicia: docker compose up -d api worker mcp")
        print("La base de datos, pgvector y el grafo NO se tocan: las referencias siguen resolviendo.")


if __name__ == "__main__":
    main()
