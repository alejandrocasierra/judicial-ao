#!/usr/bin/env bash
# Funciones comunes. Carga el archivo de entorno indicado por ENV_FILE (por defecto .env).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ENV_FILE="${ENV_FILE:-$ROOT/.env}"
[[ "$ENV_FILE" = /* ]] || ENV_FILE="$ROOT/$ENV_FILE"
log() { printf '\033[1;34m[%s]\033[0m %s\n' "$(basename "$0")" "$*"; }
die() { printf '\033[1;31m[%s] ERROR:\033[0m %s\n' "$(basename "$0")" "$*" >&2; exit 1; }
load_env() {
  [[ -f "$ENV_FILE" ]] || die "no existe $ENV_FILE (ejecuta: python3 scripts/gen_env.py)"
  eval "$(python3 "$ROOT/scripts/envload.py" --export "$ENV_FILE")"
}
