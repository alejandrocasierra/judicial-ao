#!/usr/bin/env bash
# Migra (Alembic) y siembra (opcional) VARIAS bases de datos de una sola vez.
#
# Uso:
#   bash scripts/migrate_seeds.sh .env.dev .env.staging .env.prod
#   MIGRATE_ENVS=".env.dev .env.staging .env.prod" bash scripts/migrate_seeds.sh
#   bash scripts/migrate_seeds.sh --no-seed .env.prod          # solo migraciones
#   bash scripts/migrate_seeds.sh --seed .env.quality          # fuerza semilla en staging
#   DOCKER=1 bash scripts/migrate_seeds.sh .env.prod           # dentro del contenedor api
#
# Cada archivo define su BD (POSTGRES_*) y su APP_ENV. La semilla se aplica SÓLO si
# APP_ENV no es production (el seed de demo se rechaza en prod por diseño). En staging
# se omite salvo que se pase --seed.
set -euo pipefail
cd "$(dirname "$0")/.."

SEED=1
FORCE=0
DOCKER="${DOCKER:-0}"
ENVS=()
for a in "$@"; do
  case "$a" in
    --no-seed) SEED=0 ;;
    --seed) SEED=1; FORCE=1 ;;
    --docker) DOCKER=1 ;;
    *) ENVS+=("$a") ;;
  esac
done
if [ "${#ENVS[@]}" -eq 0 ]; then
  # shellcheck disable=SC2206
  ENVS=(${MIGRATE_ENVS:-.env})
fi

PY="python3"
[ -x .venv/bin/python ] && PY="$PWD/.venv/bin/python"

for f in "${ENVS[@]}"; do
  if [ ! -f "$f" ]; then
    echo "SKIP: no existe $f"
    continue
  fi
  app_env="$(grep -E '^APP_ENV=' "$f" | tail -1 | cut -d= -f2- | tr -d ' \r' || true)"
  db="$(grep -E '^POSTGRES_DB=' "$f" | tail -1 | cut -d= -f2- | tr -d ' \r' || true)"
  echo "==> [$f] APP_ENV=${app_env:-?} POSTGRES_DB=${db:-?}"

  if [ "$DOCKER" = "1" ]; then
    docker compose --env-file "$f" exec -T api alembic -c alembic.ini upgrade head
  else
    ENV_FILE="$f" "$PY" -m alembic -c apps/api/alembic.ini upgrade head
  fi

  if [ "$SEED" = "1" ] && [ "${app_env:-}" != "production" ] && { [ "${app_env:-}" != "staging" ] || [ "$FORCE" = "1" ]; }; then
    echo "    -> seed"
    if [ "$DOCKER" = "1" ]; then
      docker compose --env-file "$f" exec -T api python -m seeds.seed || true
    else
      ( cd apps/api && ENV_FILE="$f" "$PY" -m seeds.seed ) || true
    fi
  else
    echo "    -> seed omitido (APP_ENV=${app_env:-?})"
  fi
done

echo
echo "Listo. Bases migradas. Recuerda: en producción NO se corren semillas de demo."
