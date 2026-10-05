#!/usr/bin/env bash
# Arranque en máquina de desarrollo PERSISTENTE (tu Linux).
#   bash scripts/bootstrap.sh            # usa un PostgreSQL+pgvector ya instalado (datos en .env)
#   bash scripts/bootstrap.sh --docker   # levanta postgres/redis con docker compose
# Para entornos efímeros (Claude Code web, CI) usa scripts/setup_cloud_session.sh.
source "$(dirname "$0")/common.sh"
cd "$ROOT"
[[ -f .env ]] || { log "generando .env"; python3 scripts/gen_env.py --out .env --set APP_ENV=development; }
[[ -f .env.test ]] || { log "generando .env.test"
  SUPER_PW="$(python3 -c 'import sys;sys.path.insert(0,"scripts");import envload;print(envload.load(".env")["POSTGRES_SUPERUSER_PASSWORD"])')"
  python3 scripts/gen_env.py --out .env.test --set APP_ENV=test --set POSTGRES_DB=judicial_test \
    --set POSTGRES_SUPERUSER_PASSWORD="$SUPER_PW" --set STORAGE_LOCAL_ROOT=var/test-storage \
    --set DB_OWNER_USER=judicial_owner_test --set DB_APP_USER=judicial_app_test \
    --set RATE_LIMIT_BACKEND=memory --set CELERY_TASK_ALWAYS_EAGER=true; }
  # Tests sin Redis/broker: rate limit en memoria (un solo proceso de pytest) y Celery eager.
load_env
if [[ "${1:-}" == "--docker" ]]; then
  log "docker compose: postgres + redis"
  docker compose --env-file .env up -d postgres redis
  for _ in $(seq 1 60); do pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q && break; sleep 1; done
fi
pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q || die "PostgreSQL no disponible en $POSTGRES_HOST:$POSTGRES_PORT"
python3 -m pip install -q -r requirements/dev.txt
python3 scripts/db_create.py && bash scripts/db_migrate.sh up && bash scripts/db_seed.sh
ENV_FILE="$ROOT/.env.test" python3 scripts/db_create.py --drop
log "listo ✔  API: bash scripts/dev_server.sh  |  pruebas: bash scripts/run_tests.sh"
