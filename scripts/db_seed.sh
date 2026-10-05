#!/usr/bin/env bash
# Siembra datos sintéticos + cuentas de prueba (bloqueado en staging/producción).
set -euo pipefail
cd "$(dirname "$0")/../apps/api"
python3 -m seeds.seed
