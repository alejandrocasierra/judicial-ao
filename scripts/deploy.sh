#!/usr/bin/env bash
# Despliegue en el VPS: imágenes + up + migraciones + healthcheck.
#
# Uso:
#   bash scripts/deploy.sh            # pull (si REGISTRY_IMAGE) o build, y levanta todo
#   bash scripts/deploy.sh --seed     # además siembra datos de demo (NO usar en prod real)
#   ENV_FILE=.env.production bash scripts/deploy.sh
#
# Requisitos: docker + docker compose v2, y un archivo .env en la raíz (usa
# .env.production.example como plantilla).
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-.env}"
SEED=0
[ "${1:-}" = "--seed" ] && SEED=1

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: falta $ENV_FILE. Copia .env.production.example a .env y complétalo:"
  echo "  cp .env.production.example .env && \${EDITOR:-nano} .env"
  exit 1
fi

read_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true; }

REGISTRY_IMAGE="$(read_env REGISTRY_IMAGE)"
MALWARE="$(read_env MALWARE_SCANNER)"
PUBLIC_DOMAIN="$(read_env PUBLIC_DOMAIN)"
API_PORT="$(read_env API_PORT)"; API_PORT="${API_PORT:-8000}"

DC=(docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.prod.yml)
[ "$MALWARE" = "clamav" ] && DC+=(--profile clamav)

echo "==> 1/4 Imágenes"
if [ -n "$REGISTRY_IMAGE" ]; then
  echo "    pull desde $REGISTRY_IMAGE"
  "${DC[@]}" pull
else
  echo "    build local (REGISTRY_IMAGE vacío)"
  "${DC[@]}" build
fi

echo "==> 2/4 Levantando servicios"
"${DC[@]}" up -d --remove-orphans

echo "==> 3/4 Migraciones de base de datos"
migrated=0
for i in $(seq 1 30); do
  if "${DC[@]}" exec -T api alembic -c alembic.ini upgrade head; then migrated=1; break; fi
  echo "    esperando a PostgreSQL... ($i/30)"; sleep 3
done
[ "$migrated" = "1" ] || { echo "ERROR: no se pudieron aplicar las migraciones"; exit 1; }

if [ "$SEED" = "1" ]; then
  echo "==> Sembrando datos de demo (--seed)"
  "${DC[@]}" exec -T api python -m seeds.seed || echo "    (seed omitido/errores; revisa la salida)"
fi

echo "==> 4/4 Healthcheck"
ok=0
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ "$ok" = "1" ]; then echo "    API OK"; else echo "    ADVERTENCIA: la API no respondió en /health todavía"; fi

"${DC[@]}" ps
echo
echo "Despliegue terminado."
[ -n "$PUBLIC_DOMAIN" ] && echo "Acceso: https://${PUBLIC_DOMAIN}"
