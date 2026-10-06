#!/usr/bin/env bash
# =============================================================================
# backup_gcs.sh — respaldo diario de las 3 BD a Google Cloud Storage
#
#   1. pg_dump (formato custom, comprimido) de las 3 instancias desde el
#      contenedor de PostgreSQL compartido.
#   2. Subida a gs://<BUCKET>/<PREFIX>/<fecha>/<instancia>.dump
#   3. Purga de respaldos con más de RETENTION_DAYS días (local y en GCS).
#   4. Verificación: `pg_restore --list` sobre cada dump (detecta dumps rotos).
#
# Uso:
#   bash scripts/backup_gcs.sh              # normal (lo que hace cron)
#   bash scripts/backup_gcs.sh --dry-run    # sólo informa, no sube ni borra
#   bash scripts/backup_gcs.sh --keep-days 7
#
# Cron: /etc/cron.d/backup-gcs  (15:50 diario, 10 min ANTES del deploy de 16:00)
#
# Credenciales: gcp-credentials.json (service account ocrdocumentai@welladvisor)
#   -> mismo fichero y misma vía (google-cloud-storage) que usa la app.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="judicial-infra-postgres-1"
PGUSER="postgres"
BUCKET="welladvisor"
PREFIX="judicial-ai/backups"
KEYFILE="$REPO_ROOT/gcp-credentials.json"
LOCAL_ROOT="/var/backups/judicial"
RETENTION_DAYS=30
LOG="/var/log/backup_gcs.log"

# instancia -> base de datos
declare -A DBS=(
  [site]=judicial
  [develop]=judicial_develop
  [quality]=judicial_quality
)
ORDER=(site develop quality)

DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)     DRY=1; shift ;;
    --keep-days)   RETENTION_DAYS="$2"; shift 2 ;;
    *) echo "argumento desconocido: $1" >&2; exit 2 ;;
  esac
done

ts()   { date '+%Y-%m-%d %H:%M:%S'; }
# Escribe SOLO al archivo: cron redirige el stdout de este script al mismo
# $LOG, y con `tee` cada línea salía duplicada (mismo fix que auto_deploy.sh).
log()  { printf '%s %s\n' "$(ts)" "$*" >> "$LOG"; }
fail=0

[ -f "$KEYFILE" ] || { log "ERROR: no existe $KEYFILE"; exit 1; }
docker inspect "$CONTAINER" >/dev/null 2>&1 || { log "ERROR: contenedor $CONTAINER no existe"; exit 1; }
command -v docker >/dev/null || { log "ERROR: docker no está en el PATH"; exit 1; }

FECHA="$(date +%F)"
LOCAL_DIR="$LOCAL_ROOT/$FECHA"
log "==== respaldo diario $FECHA (retención ${RETENTION_DAYS} días) ===="

if [ "$DRY" = "1" ]; then
  for inst in "${ORDER[@]}"; do
    log "  [dry] pg_dump ${DBS[$inst]} -> $LOCAL_DIR/$inst.dump -> gs://$BUCKET/$PREFIX/$FECHA/$inst.dump"
  done
  log "  [dry] purga de respaldos anteriores a $(date -d "-$RETENTION_DAYS days" +%F)"
  exit 0
fi

mkdir -p "$LOCAL_DIR"
chmod 700 "$LOCAL_ROOT" "$LOCAL_DIR"

# --- 1) pg_dump de cada instancia -------------------------------------------
for inst in "${ORDER[@]}"; do
  db="${DBS[$inst]}"
  out="$LOCAL_DIR/$inst.dump"
  log "  [1/3] pg_dump $db -> $inst.dump"
  if ! docker exec -i "$CONTAINER" pg_dump -U "$PGUSER" -Fc "$db" > "$out" 2>>"$LOG"; then
    log "    ERROR: pg_dump falló en $db"; fail=1; continue
  fi
  sz=$(stat -c%s "$out" 2>/dev/null || echo 0)
  if [ "$sz" -lt 1024 ]; then
    log "    ERROR: $inst.dump sospechosamente pequeño ($sz bytes)"; fail=1; continue
  fi
  # verificación: el dump debe poder listarse
  if docker exec -i "$CONTAINER" pg_restore -l < "$out" >/dev/null 2>>"$LOG"; then
    log "    OK  $(numfmt --to=iec --suffix=B "$sz")  (pg_restore --list verificado)"
  else
    log "    ERROR: pg_restore -l rechaza $inst.dump"; fail=1; continue
  fi
done

# --- 2) subida a GCS ---------------------------------------------------------
log "  [2/3] subida a gs://$BUCKET/$PREFIX/$FECHA/"
if /var/www/advisorlegal.co/.venv/bin/python - "$LOCAL_DIR" "$BUCKET" "$PREFIX" "$FECHA" "$KEYFILE" <<'PY' 2>>"$LOG"
import sys, os, pathlib
from google.oauth2 import service_account
from google.cloud import storage

local_dir, bucket, prefix, fecha, keyfile = sys.argv[1:6]
creds = service_account.Credentials.from_service_account_file(
    keyfile, scopes=['https://www.googleapis.com/auth/devstorage.read_write'])
c = storage.Client(credentials=creds, project='welladvisor')
b = c.bucket(bucket)
n = 0
for f in sorted(pathlib.Path(local_dir).glob("*.dump")):
    dest = f"{prefix}/{fecha}/{f.name}"
    b.blob(dest).upload_from_filename(str(f))
    print(f"    OK  {dest}  ({f.stat().st_size} bytes)")
    n += 1
print(f"    subidos: {n}")
if n == 0:
    raise SystemExit("no hay dumps que subir")
PY
then log "    subida completada"
else log "    ERROR: falló la subida a GCS"; fail=1; fi

# --- 3) purga a N días (local + GCS) ----------------------------------------
log "  [3/3] purga de respaldos con más de $RETENTION_DAYS días"
umbral="$(date -d "-$RETENTION_DAYS days" +%F)"

# local
borrados_local=0
for d in "$LOCAL_ROOT"/????-??-??; do
  [ -d "$d" ] || continue
  if [ "$(basename "$d")" \< "$umbral" ]; then
    rm -rf "$d" && { log "    local  - $(basename "$d")"; borrados_local=$((borrados_local+1)); }
  fi
done
log "    local: $borrados_local carpeta(s) eliminadas (quedan $(ls -d "$LOCAL_ROOT"/????-??-?? 2>/dev/null | wc -l))"

# GCS
if /var/www/advisorlegal.co/.venv/bin/python - "$BUCKET" "$PREFIX" "$umbral" "$KEYFILE" <<'PY' 2>>"$LOG"
import sys
from google.oauth2 import service_account
from google.cloud import storage

bucket, prefix, umbral, keyfile = sys.argv[1:5]
creds = service_account.Credentials.from_service_account_file(
    keyfile, scopes=['https://www.googleapis.com/auth/devstorage.read_write'])
c = storage.Client(credentials=creds, project='welladvisor')
viejos = [b for b in c.list_blobs(bucket, prefix=prefix + "/")
          if b.name.split("/")[2] < umbral]
for b in viejos:
    b.delete()
    print(f"    gcs   - {b.name}")
print(f"    gcs: {len(viejos)} objeto(s) eliminados")
PY
then log "    gcs completado"
else log "    ERROR: falló la purga en GCS"; fail=1; fi

# --- 4) resumen --------------------------------------------------------------
log "  [4] respaldos actuales en GCS:"
/var/www/advisorlegal.co/.venv/bin/python - "$BUCKET" "$PREFIX" "$KEYFILE" <<'PY' 2>>"$LOG" sed 's/^/    /'
import sys
from google.oauth2 import service_account
from google.cloud import storage
bucket, prefix, keyfile = sys.argv[1:4]
creds = service_account.Credentials.from_service_account_file(
    keyfile, scopes=['https://www.googleapis.com/auth/devstorage.read_only'])
c = storage.Client(credentials=creds, project='welladvisor')
tot = 0
for b in sorted(c.list_blobs(bucket, prefix=prefix + "/"), key=lambda x: x.name):
    tot += b.size
    print(f"{b.name}  {b.size} bytes")
print(f"total: {tot} bytes")
PY

if [ "$fail" = "0" ]; then log "==== respaldo OK ===="; else log "==== respaldo con ERRORES (revisar $LOG) ===="; fi
exit "$fail"
