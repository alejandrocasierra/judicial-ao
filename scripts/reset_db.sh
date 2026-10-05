#!/usr/bin/env bash
# Recrea desde cero la BD del entorno (SÓLO development/test): drop + create + migrate + seed.
source "$(dirname "$0")/common.sh"
load_env
[[ "$APP_ENV" == "development" || "$APP_ENV" == "test" ]] || die "reset prohibido en APP_ENV=$APP_ENV"
cd "$ROOT"
python3 scripts/db_create.py --drop
bash scripts/db_migrate.sh up
bash scripts/db_seed.sh
