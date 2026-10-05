#!/usr/bin/env bash
# Levanta la API en modo desarrollo con recarga. Host/puerto desde el entorno (API_HOST/API_PORT).
source "$(dirname "$0")/common.sh"
load_env
cd "$ROOT/apps/api"
exec uvicorn app.main:app --reload --host "${API_HOST}" --port "${API_PORT}"
