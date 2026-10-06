#!/usr/bin/env bash
# Crea en producción SOLO lo mínimo: organización + administrador + agentes/skills.
# No crea expedientes ni datos de demo. Idempotente.
#
# Uso:
#   bash scripts/seed_admin.sh
#   ENV_FILE=.env.production bash scripts/seed_admin.sh
#
# Requiere en el .env: BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD (y opcional
# BOOTSTRAP_ADMIN_NAME, BOOTSTRAP_ORG_NAME, BOOTSTRAP_ORG_SLUG).
set -euo pipefail
cd "$(dirname "$0")/.."

export ENV_FILE="${ENV_FILE:-.env}"   # compose lo usa en env_file: ${ENV_FILE:-.env}
FILES=(-f docker-compose.yml)
[ -f docker-compose.prod.yml ] && FILES+=(-f docker-compose.prod.yml)

echo "Creando organización + administrador + agentes/skills de sistema..."
docker compose --env-file "$ENV_FILE" "${FILES[@]}" exec -T api python -m seeds.bootstrap_admin
