#!/usr/bin/env bash
# Configura CORS del bucket de GCS para permitir la SUBIDA DIRECTA desde el navegador.
#
# ¿Por qué? Los archivos > 90 MB no pasan por la API (límite de Cloudflare): el
# frontend pide una URL prefirmada y hace `PUT` directo al bucket. Para que el
# navegador pueda hacer ese PUT, el bucket debe responder al preflight OPTIONS con
# las cabeceras CORS; si no, falla con
# "No 'Access-Control-Allow-Origin' header is present".
#
# Estrategia (en orden):
#   1. gcloud (si está instalado y autenticado).
#   2. gsutil (si está instalado y autenticado).
#   3. el contenedor `api` (tiene google-cloud-storage + credenciales) — ideal
#      para el despliegue, sin instalar nada en el servidor.
#
# Uso:  bash scripts/setup_gcs_cors.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# Carga .env si existe (última definición gana).
if [ -f "${ENV_FILE:-.env}" ]; then
  set -a
  # shellcheck disable=SC1091
  . "./${ENV_FILE:-.env}"
  set +a
fi

BUCKET="${S3_BUCKET:-${GCS_BUCKET:-}}"
CORS_FILE="infra/gcp/storage-cors.json"
DC=(docker compose --env-file "${ENV_FILE:-.env}" -f docker-compose.yml)

if [ -z "$BUCKET" ]; then
  echo "ERROR: define S3_BUCKET (o GCS_BUCKET) en ${ENV_FILE:-.env}" >&2
  exit 1
fi

echo "Configurando CORS del bucket gs://$BUCKET …"

if command -v gcloud >/dev/null 2>&1; then
  gcloud storage buckets update "gs://$BUCKET" --cors-file="$CORS_FILE"
  gcloud storage buckets describe "gs://$BUCKET" --format="default(cors_config)"
elif command -v gsutil >/dev/null 2>&1; then
  gsutil cors set "$CORS_FILE" "gs://$BUCKET"
  gsutil cors get "gs://$BUCKET"
elif "${DC[@]}" ps api >/dev/null 2>&1; then
  # El contenedor api tiene las credenciales y google-cloud-storage.
  "${DC[@]}" exec -T api python /srv/scripts/set_gcs_cors.py
else
  echo "ERROR: no hay gcloud/gsutil y el contenedor 'api' no está corriendo." >&2
  echo "       Levanta el stack (docker compose up -d api) y reintenta." >&2
  exit 1
fi

echo "OK. Recuerda incluir el dominio público en CORS_ALLOWED_ORIGINS de cada entorno."
