#!/usr/bin/env bash
# Exporta TODA la instancia para migrarla a otro servidor (VPS/GCP):
#   - volcado de PostgreSQL (incluye documentos, pgvector y grafo)
#   - los archivos originales de var/storage (PDFs, imágenes, videos)
#
# Uso:
#   bash scripts/export_data.sh [carpeta_destino]
#
# Genera: <destino>/judicial.dump (pg_dump -Fc) + <destino>/storage.tgz + manifest.txt
# ¡El resultado contiene DATOS REALES: muévelo por un canal privado y no lo subas a Git!
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-.env}"
FILES=(-f docker-compose.yml)
[ -f docker-compose.prod.yml ] && FILES+=(-f docker-compose.prod.yml)
DC=(docker compose --env-file "$ENV_FILE" "${FILES[@]}")

read_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true; }
DB="$(read_env POSTGRES_DB)"; DB="${DB:-judicial}"
OUT="${1:-backup/judicial-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"

echo "==> 1/3 Dump de la base de datos '$DB' (esquema + datos + pgvector + grafo)"
"${DC[@]}" exec -T postgres sh -c "pg_dump -U \"\$POSTGRES_USER\" -Fc -f /tmp/judicial.dump \"$DB\""
"${DC[@]}" cp postgres:/tmp/judicial.dump "$OUT/judicial.dump"

echo "==> 2/3 Storage (var/storage)"
if [ -d var/storage ]; then
  tar czf "$OUT/storage.tgz" -C var storage
else
  echo "    (no existe var/storage; ¿usas S3/GCS? En ese caso no hace falta copiarlo)"
fi

echo "==> 3/3 Manifest"
{
  echo "db=$DB"
  echo "created=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "dump=judicial.dump (pg_dump -Fc)"
  echo "storage=storage.tgz (contenido de var/storage)"
} > "$OUT/manifest.txt"

echo
echo "Exportado en: $OUT"
du -sh "$OUT" 2>/dev/null || true
echo "Muévelo al VPS por un canal privado (scp/rsync/gcloud) y allí corre:"
echo "  bash scripts/import_data.sh $OUT"
