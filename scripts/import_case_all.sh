#!/usr/bin/env bash
# Carga el MISMO expediente en site, develop y quality (una vez exportado con export_case.py).
# Auto-resuelve la organización destino de cada instancia (la primera de su BD).
#
# Uso:
#   1) exporta desde tu instancia origen (local):
#        python scripts/export_case.py --case-id <uuid> --org-id <uuid> --out casos/<uuid>
#   2) importa en las 3:
#        bash scripts/import_case_all.sh --export-dir casos/<uuid>
#
# Opciones:
#   --instances "site develop quality"     (por defecto las tres)
#   --site-org <uuid> --develop-org <uuid> --quality-org <uuid>   (forzar organización destino)
#   --no-files                              (no restaurar archivos de storage)
#
# Requiere que las instancias estén levantadas (modo compartido o aislado).
set -euo pipefail
cd "$(dirname "$0")/.."

EXPORT_DIR=""
INSTANCES="site develop quality"
NO_FILES=0
declare -A ORG=()

while [ $# -gt 0 ]; do
  case "$1" in
    --export-dir)  EXPORT_DIR="$2"; shift 2 ;;
    --instances)   INSTANCES="$2"; shift 2 ;;
    --site-org)    ORG[site]="$2"; shift 2 ;;
    --develop-org) ORG[develop]="$2"; shift 2 ;;
    --quality-org) ORG[quality]="$2"; shift 2 ;;
    --no-files)    NO_FILES=1; shift ;;
    *) echo "arg desconocido: $1"; exit 2 ;;
  esac
done

[ -n "$EXPORT_DIR" ] || { echo "ERROR: falta --export-dir <carpeta exportada>"; exit 2; }
[ -d "$EXPORT_DIR" ] || { echo "ERROR: no existe la carpeta $EXPORT_DIR"; exit 2; }

env_for() {
  case "$1" in
    site) echo ".env.advisorlegal" ;;
    develop) echo ".env.develop" ;;
    quality) echo ".env.quality" ;;
    *) echo "" ;;
  esac
}

resolve_org() { # env-file -> uuid de la primera organización
  docker compose --env-file "$1" exec -T api python -c '
import sys
sys.path.insert(0, "/srv/scripts")
import case_transfer as ct
conn = ct.connect()
cur = conn.cursor()
cur.execute("SELECT id FROM organizations ORDER BY created_at LIMIT 1")
row = cur.fetchone()
print(str(row[0]) if row else "")
conn.close()
' 2>/dev/null | tr -d "\r\n"
}

for inst in $INSTANCES; do
  E="$(env_for "$inst")"
  if [ -z "$E" ] || [ ! -f "$E" ]; then
    echo "SKIP $inst: falta el archivo de entorno"
    continue
  fi
  echo "=== $inst ($E) ==="
  org="${ORG[$inst]:-}"
  if [ -z "$org" ]; then
    org="$(resolve_org "$E")"
  fi
  if [ -z "$org" ]; then
    echo "  ❌ no pude resolver la organización destino (¿corriste seed_admin.sh?). Usa --${inst}-org <uuid>."
    continue
  fi
  echo "  org destino: $org"
  echo "  copiando expediente al contenedor..."
  docker compose --env-file "$E" cp "$EXPORT_DIR" api:/tmp/case-export >/dev/null
  args=(--in /tmp/case-export --org-id "$org")
  [ "$NO_FILES" = "1" ] && args+=(--no-files)
  docker compose --env-file "$E" exec -T api python /srv/scripts/import_case.py "${args[@]}"
  # Extrae y crea las PARTES (demandante/demandado/…) desde los encabezados de los autos.
  CASEID="$(sed -n 's/.*"case_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$EXPORT_DIR/manifest.json" | head -1)"
  if [ -n "$CASEID" ]; then
    echo "  extrayendo partes del expediente..."
    docker compose --env-file "$E" exec -T api \
      python /srv/scripts/extract_parties.py --case-id "$CASEID" --org-id "$org" --confirm || true
  fi
  docker compose --env-file "$E" exec -T api rm -rf /tmp/case-export || true
done

echo
echo "Listo. Abre el proceso en el dashboard de cada instancia."
