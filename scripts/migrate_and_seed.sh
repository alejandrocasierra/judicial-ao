#!/usr/bin/env bash
# Aplica MIGRACIONES y SIEMBRA las Partes/roles del proceso (idempotente).
#
# Uso (en el servidor, desde la raíz del repo, con el stack ya desplegado):
#   bash scripts/migrate_and_seed.sh               # site (con reverse proxy)
#   bash scripts/migrate_and_seed.sh --shared      # infra compartida
#   bash scripts/migrate_and_seed.sh --no-proxy    # develop/quality
#
# Variables opcionales para localizar el caso:
#   CASE_NUMBER (por defecto 11001310302120180036100) o CASE_ID / ORG_ID
#
# Ejemplo:
#   ENV_FILE=.env.advisorlegal CASE_NUMBER=11001310302120180036100 bash scripts/migrate_and_seed.sh --shared
set -euo pipefail
cd "$(dirname "$0")/.."

export ENV_FILE="${ENV_FILE:-.env}"
SHARED=0
NO_PROXY=0
for a in "$@"; do
  case "$a" in
    --shared) SHARED=1 ;;
    --no-proxy) NO_PROXY=1 ;;
  esac
done

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: falta $ENV_FILE" >&2
  exit 1
fi

DC=(docker compose --env-file "$ENV_FILE" -f docker-compose.yml)
[ "$NO_PROXY" = "0" ] && DC+=(-f docker-compose.prod.yml)
[ "$SHARED" = "1" ] && DC+=(-f infra/docker/docker-compose.shared-app.yml)

echo "==> 1/2 Migraciones de base de datos (alembic upgrade head)"
"${DC[@]}" exec -T api alembic -c alembic.ini upgrade head

echo "==> 2/2 Sembrado de Partes y roles del proceso"
"${DC[@]}" exec -T api python /srv/scripts/seed_parties.py

echo "Listo."
