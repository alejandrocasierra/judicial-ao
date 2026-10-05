#!/usr/bin/env bash
# Aplica migraciones (up) o revierte (down). Uso: scripts/db_migrate.sh [up|down]
set -euo pipefail
cd "$(dirname "$0")/.."
cmd="${1:-up}"
if [[ "$cmd" == "up" ]]; then alembic -c apps/api/alembic.ini upgrade head
elif [[ "$cmd" == "down" ]]; then alembic -c apps/api/alembic.ini downgrade base
else echo "usage: $0 [up|down]"; exit 2; fi
