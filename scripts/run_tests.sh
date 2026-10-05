#!/usr/bin/env bash
# Ejecuta la batería de pruebas. Uso:
#   bash scripts/run_tests.sh [all|fast|unit|integration|security|behavior|static] [args extra de pytest]
#   fast = unit + static (sin base de datos).  Siempre usa .env.test (APP_ENV=test).
source "$(dirname "$0")/common.sh"
cd "$ROOT"
export ENV_FILE="${ENV_FILE_TEST:-$ROOT/.env.test}"
[[ -f "$ENV_FILE" ]] || die "falta .env.test — ejecuta: bash scripts/setup_cloud_session.sh"
suite="${1:-all}"; shift || true
case "$suite" in
  all)         python3 -m pytest "$@" ;;
  fast)        SKIP_DB_RESET=1 python3 -m pytest -m "unit or static" "$@" ;;
  unit|static) SKIP_DB_RESET=1 python3 -m pytest -m "$suite" "$@" ;;
  integration|security|behavior) python3 -m pytest -m "$suite" "$@" ;;
  *) die "suite desconocida: $suite" ;;
esac
