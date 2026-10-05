#!/usr/bin/env bash
# Descarga un volcado desde un bucket de Google Cloud Storage y lo importa.
# Deja un VPS nuevo con los datos en un comando.
#
# Uso:
#   bash scripts/fetch_and_import.sh gs://mi-bucket/judicial
#
# Requiere: Google Cloud SDK (gcloud o gsutil) y permisos de lectura sobre el bucket
# (p. ej. la service account del VPS con rol roles/storage.objectViewer).
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:?Uso: bash scripts/fetch_and_import.sh gs://mi-bucket/prefix}"
WORK="${2:-backup/fetch-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$WORK"

if command -v gcloud >/dev/null 2>&1; then
  echo "==> Descargando con gcloud storage"
  gcloud storage cp "$SRC/judicial.dump" "$SRC/storage.tgz" "$SRC/manifest.txt" "$WORK/" 2>/dev/null \
    || gcloud storage cp --recursive "$SRC" "$WORK/"
elif command -v gsutil >/dev/null 2>&1; then
  echo "==> Descargando con gsutil"
  gsutil -m cp -r "$SRC/*" "$WORK/"
else
  echo "ERROR: instala el Google Cloud SDK (gcloud/gsutil) o descarga manualmente y usa import_data.sh"
  exit 1
fi

[ -f "$WORK/judicial.dump" ] || { echo "ERROR: no se encontró judicial.dump en $WORK"; exit 1; }
bash scripts/import_data.sh "$WORK"
