#!/usr/bin/env bash
# Hook SessionStart de Claude Code. En sesiones remotas (web) prepara todo automáticamente;
# en local sólo actúa si falta .env (no toca un entorno ya configurado).
# Salida breve: Claude Code la añade al contexto de la sesión.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/var/session_setup.log"; mkdir -p "$ROOT/var"
if [[ "${CLAUDE_CODE_REMOTE:-}" == "true" || ! -f "$ROOT/.env" ]]; then
  if bash "$ROOT/scripts/setup_cloud_session.sh" >"$LOG" 2>&1; then
    echo "Entorno listo: PostgreSQL+pgvector, .env/.env.test generados, BD migrada y sembrada."
    echo "Pruebas: bash scripts/run_tests.sh [all|fast|unit|integration|security|behavior|static]"
    echo "Cuentas de prueba: ver docs/TEST_ACCOUNTS.md (contraseña = SEED_DEFAULT_PASSWORD en .env; no la imprimas)."
  else
    echo "ATENCIÓN: la preparación del entorno falló. Revisa var/session_setup.log (últimas líneas):"
    tail -n 15 "$LOG"
  fi
else
  echo "Entorno local existente detectado (.env presente); no se modificó nada."
fi
exit 0
