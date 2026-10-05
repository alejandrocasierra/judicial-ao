#!/usr/bin/env bash
# =============================================================================
# Prepara un entorno EFÍMERO (Claude Code en la web, Codespaces, contenedor CI)
# desde cero y de forma idempotente:
#   1. instala PostgreSQL + pgvector si faltan (apt)
#   2. arranca PostgreSQL y fija la contraseña del superusuario desde el .env
#   3. genera .env (desarrollo) y .env.test (pruebas) con secretos aleatorios
#   4. instala dependencias Python
#   5. crea roles/BD, migra y siembra (dev y test)
# Uso:  bash scripts/setup_cloud_session.sh [--no-seed] [--skip-apt]
# Se invoca automáticamente desde .claude/settings.json (hook SessionStart).
# =============================================================================
source "$(dirname "$0")/common.sh"
cd "$ROOT"
SEED=1; APT=1
for a in "$@"; do case "$a" in --no-seed) SEED=0;; --skip-apt) APT=0;; esac; done
SUDO=""; [[ $(id -u) -ne 0 ]] && command -v sudo >/dev/null && SUDO="sudo"

# ---- 1. PostgreSQL + pgvector -------------------------------------------------
if [[ $APT -eq 1 ]] && ! command -v pg_ctlcluster >/dev/null 2>&1; then
  log "instalando PostgreSQL (apt)…"
  $SUDO apt-get update -qq
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq postgresql postgresql-contrib >/dev/null
fi
PGMAJOR="$(ls /usr/lib/postgresql 2>/dev/null | sort -n | tail -1)"
[[ -n "$PGMAJOR" ]] || die "PostgreSQL no está instalado"
if [[ $APT -eq 1 ]] && [[ ! -f "/usr/share/postgresql/$PGMAJOR/extension/vector.control" ]]; then
  log "instalando pgvector para PostgreSQL $PGMAJOR…"
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq "postgresql-$PGMAJOR-pgvector" >/dev/null
fi

# ---- 2. Archivos de entorno ----------------------------------------------------
if [[ ! -f .env ]]; then
  log "generando .env (secretos aleatorios)"
  # Clúster ya existente con contraseña conocida: SETUP_PG_SUPERUSER_PASSWORD=<clave> bash scripts/setup_cloud_session.sh
  python3 scripts/gen_env.py --out .env --set APP_ENV=development \
    ${SETUP_PG_SUPERUSER_PASSWORD:+--set POSTGRES_SUPERUSER_PASSWORD="$SETUP_PG_SUPERUSER_PASSWORD"}
fi
if [[ ! -f .env.test ]]; then
  SUPER_PW="$(ENV_FILE=.env python3 -c 'import sys;sys.path.insert(0,"scripts");import envload;print(envload.load(".env")["POSTGRES_SUPERUSER_PASSWORD"])')"
  log "generando .env.test"
  python3 scripts/gen_env.py --out .env.test --set APP_ENV=test --set POSTGRES_DB="${TEST_DB_NAME:-judicial_test}" \
    --set POSTGRES_SUPERUSER_PASSWORD="$SUPER_PW" --set STORAGE_LOCAL_ROOT=var/test-storage \
    --set DB_OWNER_USER=judicial_owner_test --set DB_APP_USER=judicial_app_test \
    --set RATE_LIMIT_BACKEND=memory --set CELERY_TASK_ALWAYS_EAGER=true
    # roles propios: no pisan los de dev. Tests sin Redis/broker: rate limit en memoria
    # (por proceso basta en un único proceso de pytest) y Celery en modo eager.
fi
ENV_FILE="$ROOT/.env"; load_env

# ---- 3. Arrancar PostgreSQL y fijar contraseña del superusuario -------------------
if ! pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q; then
  log "arrancando PostgreSQL $PGMAJOR"
  $SUDO pg_ctlcluster "$PGMAJOR" main start 2>/dev/null || $SUDO service postgresql start
  for _ in $(seq 1 30); do pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q && break; sleep 1; done
fi
pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -q || die "PostgreSQL no responde en $POSTGRES_HOST:$POSTGRES_PORT"
if ! PGPASSWORD="$POSTGRES_SUPERUSER_PASSWORD" psql -w -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_SUPERUSER" -d postgres -tAc "select 1" >/dev/null 2>&1; then
  log "fijando contraseña del superusuario '$POSTGRES_SUPERUSER' (sólo entorno efímero)"
  SQL="ALTER USER \"$POSTGRES_SUPERUSER\" WITH PASSWORD '$POSTGRES_SUPERUSER_PASSWORD'"
  done_pw=0
  for sock in /var/run/postgresql /tmp; do
    [[ -S "$sock/.s.PGSQL.$POSTGRES_PORT" ]] || continue
    if [[ $(id -un) == postgres ]]; then psql -w -h "$sock" -p "$POSTGRES_PORT" -q -c "$SQL" && done_pw=1
    elif [[ -n "$SUDO" || $(id -u) -eq 0 ]]; then
      (cd /tmp && ${SUDO:-} su postgres -s /bin/bash -c "psql -w -h '$sock' -p '$POSTGRES_PORT' -q -c \"$SQL\"") && done_pw=1
    fi
    [[ $done_pw -eq 1 ]] && break
  done
  [[ $done_pw -eq 1 ]] || die "no se pudo fijar la contraseña del superusuario (ajusta POSTGRES_SUPERUSER_PASSWORD en .env y .env.test)"
fi

# ---- 4. Dependencias Python ---------------------------------------------------
log "instalando dependencias Python"
python3 -m pip install -q -r requirements/dev.txt 2>/dev/null || python3 -m pip install -q --break-system-packages -r requirements/dev.txt

# ---- 5. Base de datos dev + test -----------------------------------------------
for f in .env .env.test; do
  log "[$f] creando roles/BD, migrando${SEED:+ y sembrando}"
  DROP=""; [[ "$f" == ".env.test" ]] && DROP="--drop"   # la BD de pruebas es desechable
  ENV_FILE="$ROOT/$f" python3 scripts/db_create.py $DROP
  ENV_FILE="$ROOT/$f" bash scripts/db_migrate.sh up
  if [[ $SEED -eq 1 ]]; then ENV_FILE="$ROOT/$f" bash scripts/db_seed.sh; fi
done
log "listo ✔  — prueba: bash scripts/run_tests.sh   |   servidor: bash scripts/dev_server.sh"
