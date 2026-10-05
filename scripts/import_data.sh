#!/usr/bin/env bash
# Importa una instancia exportada con scripts/export_data.sh a ESTE servidor:
#   - recrea la base de datos y restaura el volcado (documentos, pgvector, grafo)
#   - restaura los archivos originales en var/storage
#
# Uso:
#   bash scripts/import_data.sh <carpeta-del-dump>
#
# ATENCIÓN: REEMPLAZA la base de datos actual y el storage. Úsalo en una instancia
# nueva o vacía. Requiere que los roles (DB_OWNER_USER/DB_APP_USER) ya existan
# (los crea scripts/bootstrap.sh / bootstrap --docker).
set -euo pipefail
cd "$(dirname "$0")/.."

IN="${1:?Uso: bash scripts/import_data.sh <carpeta-del-dump>}"
[ -f "$IN/judicial.dump" ] || { echo "ERROR: falta $IN/judicial.dump"; exit 1; }

ENV_FILE="${ENV_FILE:-.env}"
FILES=(-f docker-compose.yml)
[ -f docker-compose.prod.yml ] && FILES+=(-f docker-compose.prod.yml)
DC=(docker compose --env-file "$ENV_FILE" "${FILES[@]}")

read_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true; }
DB="$(read_env POSTGRES_DB)"; DB="${DB:-judicial}"

echo "==> Deteniendo servicios que usan la BD"
"${DC[@]}" stop api worker mcp 2>/dev/null || true

echo "==> Copiando el volcado al contenedor"
"${DC[@]}" cp "$IN/judicial.dump" postgres:/tmp/judicial.dump

echo "==> Recreando la base '$DB' y restaurando (esquema + datos + pgvector + grafo)"
"${DC[@]}" exec -T postgres sh -c "dropdb -U \"\$POSTGRES_USER\" --force --if-exists \"$DB\" && createdb -U \"\$POSTGRES_USER\" \"$DB\""
"${DC[@]}" exec -T postgres sh -c "pg_restore -U \"\$POSTGRES_USER\" -d \"$DB\" /tmp/judicial.dump"

echo "==> Restaurando archivos originales (var/storage)"
if [ -f "$IN/storage.tgz" ]; then
  tar xzf "$IN/storage.tgz" -C var
else
  echo "    (sin storage.tgz; si usas S3/GCS, los originales ya están en el bucket)"
fi

echo "==> Levantando servicios"
"${DC[@]}" start api worker mcp 2>/dev/null || "${DC[@]}" up -d

echo "==> Verificando migraciones (por si el volcado es de una versión anterior)"
"${DC[@]}" exec -T api alembic -c alembic.ini upgrade head || true

echo
echo "Importación terminada. Revisa la salud de la API:  docker compose ps"
