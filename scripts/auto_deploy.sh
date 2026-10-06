#!/usr/bin/env bash
# =============================================================================
# auto_deploy.sh — chequeo automático de cambios + redespliegue
#
#   - Cada ejecución (cron: cada 10 min) hace `git fetch` en CADA clonado y
#     compara HEAD con su upstream ("cada rama asociada").
#   - Si NO hay cambios y todo está sano  => no hace NADA (sale en silencio).
#   - Si HAY cambios (o el último deploy falló) => pull --rebase + redespliegue
#     completo con `scripts/deploy_all.sh` (build → infra → BDs → apps → Caddy).
#   - Si hay contenedores caídos (p. ej. tras un reboot) => los levanta,
#     aunque no haya cambios de git.
#
# Uso:
#   bash scripts/auto_deploy.sh            # comportamiento normal (cron)
#   bash scripts/auto_deploy.sh --force    # redespliega aunque no haya cambios
#   bash scripts/auto_deploy.sh --dry-run  # sólo informa, no toca nada
#
# Ficheros:
#   /var/log/auto_deploy.log               log (rotado a 1000 líneas)
#   /var/run/auto_deploy.lock              bloqueo anti-solape (flock)
#   /var/lib/auto_deploy/pending           flag: el último deploy falló => reintenta
# =============================================================================
set -uo pipefail

ROOT="/var/www/advisorlegal.co"
DEPLOY_SRC="$ROOT/judicial-ao-site"          # fuente del build (deploy_all.sh)
# Este script vive EN el repo (judicial-ao-site/scripts/) => versionado.
# Clonados y su rama asociada: se toma el upstream real de cada uno (@{u}),
# así si una rama cambia de跟踪 no hay que tocar este script.
CLONES=(judicial-ao-site judicial-ao-develop judicial-ao-quality)

LOG="${AUTO_DEPLOY_LOG:-/var/log/auto_deploy.log}"
LOCK="/var/run/auto_deploy.lock"
STATE_DIR="/var/lib/auto_deploy"
PENDING="$STATE_DIR/pending"
MAX_LOG_LINES=1000

FORCE=0; DRY=0
for a in "$@"; do case "$a" in
  --force)   FORCE=1 ;;
  --dry-run) DRY=1 ;;
  *) echo "argumento desconocido: $a" >&2; exit 2 ;;
esac; done

mkdir -p "$STATE_DIR"
ts() { date -u '+%Y-%m-%d %H:%M:%S'; }
# Escribe SOLO en el archivo (sin stdout): cron redirige el stdout del script
# al mismo $LOG, y con `tee` cada línea salía duplicada.
log() { printf '%s %s\n' "$(ts)" "$*" >> "$LOG"; }

# --- rotación del log -------------------------------------------------------
rotar_log() {
  [ -f "$LOG" ] || return 0
  local n; n=$(wc -l < "$LOG")
  if [ "$n" -gt $((MAX_LOG_LINES * 3)) ]; then
    tail -n "$MAX_LOG_LINES" "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  fi
}

# --- bloqueo: si otro deploy sigue corriendo, no solapar ---------------------
exec 9>"$LOCK"
if ! flock -n 9; then
  # Sin log: esto pasa 9 de cada 10 minutos cuando un deploy va largo.
  exit 0
fi

rotar_log

# --- 0) saneamiento previo --------------------------------------------------
if ! command -v git >/dev/null || ! command -v docker >/dev/null; then
  log "ERROR: falta git o docker en el PATH"; exit 1
fi
if ! systemctl is-active --quiet docker; then
  log "docker no está activo -> intentando arrancarlo"
  [ "$DRY" = "1" ] || systemctl start docker
fi
if [ ! -d "$DEPLOY_SRC/.git" ]; then
  log "ERROR: no existe el repositorio $DEPLOY_SRC"; exit 1
fi

# --- 1) fetch + comparación en CADA clonado ---------------------------------
CAMBIOS=0
FALLAS=0
declare -A NUEVO
for c in "${CLONES[@]}"; do
  dir="$ROOT/$c"
  if [ ! -d "$dir/.git" ]; then log "AVISO: $c no es un repositorio, se omite"; continue; fi

  # trabajo local sucio => no se puede rebasar con seguridad
  if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
    log "AVISO: $c tiene cambios locales sin commitear; se omite su pull"
    FALLAS=$((FALLAS+1)); continue
  fi

  up="$(git -C "$dir" rev-parse --abbrev-ref '@{u}' 2>/dev/null || true)"
  if [ -z "$up" ]; then log "AVISO: $c no tiene upstream configurado, se omite"; continue; fi

  # 2 intentos: si otro proceso hace fetch a la vez, git falla con
  # "cannot lock ref" y un reintento a los 3 s lo resuelve.
  ok=0
  for try in 1 2; do
    if git -C "$dir" fetch --quiet origin 2>>"$LOG"; then ok=1; break; fi
    [ "$try" = "1" ] && sleep 3
  done
  if [ "$ok" = "0" ]; then
    log "AVISO: fetch falló en $c tras 2 intentos (¿red/token?)"; FALLAS=$((FALLAS+1)); continue
  fi

  local_h="$(git -C "$dir" rev-parse HEAD)"
  remote_h="$(git -C "$dir" rev-parse '@{u}' 2>/dev/null || echo "")"
  if [ -z "$remote_h" ]; then log "AVISO: no pude leer upstream de $c"; continue; fi

  # nº de commits remotos pendientes (se usa en las dos condiciones de abajo)
  n=$(git -C "$dir" rev-list --count "HEAD..@{u}")

  # HEAD adelantado respecto al remoto (commit local sin push): NO son "cambios
  # remotos", no hay nada que bajar. Evita un redespliegue innecesario.
  if [ "$local_h" != "$remote_h" ] && [ "$n" = "0" ]; then
    log "AVISO: $c está adelantado a $up (commit local sin push); no se redespliega"
    continue
  fi

  if [ "$local_h" != "$remote_h" ] && [ "$n" -gt 0 ]; then
    log "$c: $n commit(s) nuevos en $up  $(git -C "$dir" log -1 --format='%h %s' "$up" | cut -c1-70)"
    NUEVO[$c]=1
    CAMBIOS=1
    if [ "$DRY" = "0" ]; then
      if ! git -C "$dir" pull --quiet --rebase origin "$(basename "$up")" >>"$LOG" 2>&1; then
        log "ERROR: pull --rebase falló en $c -> ver $LOG"
        git -C "$dir" rebase --abort >/dev/null 2>&1 || true
        FALLAS=$((FALLAS+1))
      fi
    fi
  fi
done

# --- 2) estado de los contenedores (soporte de reinicios) -------------------
CAIDOS=""
if [ "$DRY" = "0" ] && docker info >/dev/null 2>&1; then
  CAIDOS="$(docker ps -a --filter 'status=exited' --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')"
fi

# --- 3) decisión ------------------------------------------------------------
PENDIENTE=0; [ -f "$PENDING" ] && PENDIENTE=1

if [ "$CAMBIOS" = "0" ] && [ "$FORCE" = "0" ] && [ "$PENDIENTE" = "0" ]; then
  if [ -n "${CAIDOS// /}" ]; then
    log "sin cambios de git, pero contenedores caídos: $CAIDOS => relanzando"
    [ "$DRY" = "1" ] || docker start $CAIDOS >>"$LOG" 2>&1
    log "relanzados"
  else
    # Sin cambios y todo sano: no se hace nada.
    if [ "$DRY" = "1" ]; then log "[dry-run] sin cambios, no haría nada"; fi
    exit 0
  fi
  exit 0
fi

[ "$CAMBIOS" = "1" ] && log "==> cambios detectados: ${!NUEVO[*]}"
[ "$PENDIENTE" = "1" ] && [ "$CAMBIOS" = "0" ] && log "==> reintento: el deploy anterior falló"
[ "$FORCE" = "1" ] && [ "$CAMBIOS" = "0" ] && log "==> --force: redespliegue pedido manualmente"

if [ "$DRY" = "1" ]; then
  log "[dry-run] NO se redespliega (terminaría aquí)"
  exit 0
fi

# --- 4) redespliegue --------------------------------------------------------
# Flag ANTES de empezar: si el proceso muere a mitad, el próximo cron reintenta.
touch "$PENDING"
log "==> redespliegue: bash scripts/deploy_all.sh (desde $DEPLOY_SRC)"
t0=$(date +%s)

if ( cd "$DEPLOY_SRC" && bash scripts/deploy_all.sh ) >>"$LOG" 2>&1; then
  rm -f "$PENDING"
  log "==> deploy OK en $(( $(date +%s) - t0 ))s"
else
  rc=$?
  log "==> deploy FALLÓ (rc=$rc) -> se reintentará en el próximo cron. Ver $LOG"
  FALLAS=$((FALLAS+1))
fi

# --- 5) verificación rápida -------------------------------------------------
sleep 5
for d in advisorlegal.co develop.advisorlegal.co quality.advisorlegal.co; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "https://$d/health" 2>/dev/null || echo 000)
  if [ "$code" = "200" ]; then log "   OK   https://$d/health -> 200"
  else log "   MAL  https://$d/health -> $code"; FALLAS=$((FALLAS+1)); fi
done

exit "$(( FALLAS > 1 ? 1 : FALLAS ))"
