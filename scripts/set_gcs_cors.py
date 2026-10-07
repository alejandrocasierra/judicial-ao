"""Aplica la política CORS al bucket de GCS para permitir la SUBIDA DIRECTA desde el navegador.

Idempotente. Pensado para ejecutarse DENTRO del contenedor `api` (que ya tiene
`google-cloud-storage` y las credenciales `GOOGLE_APPLICATION_CREDENTIALS`), de modo
que el despliegue pueda configurarlo sin instalar gcloud en el servidor.

No hace nada si STORAGE_BACKEND != "gcs" (p. ej. local/s3).

Orígenes permitidos:
  - CORS_ALLOWED_ORIGINS (lista separada por comas)
  - https://PUBLIC_DOMAIN (si está definido)
"""
from __future__ import annotations

import os
import sys


def _origins() -> list[str]:
    out: list[str] = []
    for o in (os.environ.get("CORS_ALLOWED_ORIGINS") or "").split(","):
        o = o.strip()
        if o and o not in out:
            out.append(o)
    domain = (os.environ.get("PUBLIC_DOMAIN") or "").strip()
    if domain:
        url = f"https://{domain}"
        if url not in out:
            out.append(url)
    return out


def main() -> int:
    backend = (os.environ.get("STORAGE_BACKEND") or "").strip().lower()
    if backend != "gcs":
        print(f"STORAGE_BACKEND='{backend or '(vacío)'}' -> no es GCS; CORS no aplica.")
        return 0

    bucket_name = os.environ.get("S3_BUCKET") or os.environ.get("GCS_BUCKET")
    if not bucket_name:
        print("ERROR: S3_BUCKET/GCS_BUCKET no definido.")
        return 1

    origins = _origins()
    if not origins:
        print("ERROR: no hay orígenes (define CORS_ALLOWED_ORIGINS o PUBLIC_DOMAIN).")
        return 1

    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    # Merge con la política existente: los entornos comparten bucket, así que no
    # debemos borrar los orígenes ya configurados por otro entorno (dev/quality/prod).
    try:
        bucket.reload()
        existing = bucket.cors or []
    except Exception:  # noqa: BLE001
        existing = []
    current: set[str] = set()
    for entry in existing:
        for o in entry.get("origin", []) or []:
            current.add(o)
    current.update(origins)

    cors = [{
        "origin": sorted(current),
        "method": ["GET", "HEAD", "PUT", "POST", "OPTIONS"],
        "responseHeader": ["Content-Type", "Content-MD5", "ETag",
                           "x-goog-content-length-range", "x-goog-meta-*"],
        "maxAgeSeconds": 3600,
    }]
    bucket.cors = cors
    bucket.patch()
    print(f"OK: CORS aplicado a gs://{bucket_name}")
    print("orígenes:", ", ".join(sorted(current)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
