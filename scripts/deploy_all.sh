#!/usr/bin/env bash
# Despliegue COMPLETO en el VPS: modo COMPARTIDO (un Postgres/Redis/ClamAV) + Caddy único.
# Encadena los pasos 4–12 de docs/DEPLOY_GCP.md en un solo comando.
#
# Uso:
#   bash scripts/deploy_all.sh
#   bash scripts/deploy_all.sh --export-dir casos/<uuid> \
#       --case-id <uuid> --org-id <uuid> --user-id <uuid> --gemini-key <KEY> --seed-models
#
# Opciones:
#   --instances "site develop quality"   instancias a desplegar (por defecto las tres)
#   --no-build                           no reconstruir imágenes
#   --seed-quality                       sembrar también quality (staging)
#   --seed-models --gemini-key <KEY>     dar de alta los modelos Gemini (site)
#   --export-dir <dir>                   cargar el expediente en las 3 (import_case_all.sh)
#   --case-id/--org-id/--user-id <uuid>  reindexar embeddings de site (post-despliegue)
#   --skip-verify                        no ejecutar verify_deploy.sh
#
# Aislado (en vez de compartido): usa docs/DEPLOY_GCP.md §8 con deploy.sh por instancia.
set -euo pipefail
cd "$(dirname "$0")/.."

INSTANCES="site develop quality"
BUILD=1
SEED_QUALITY=0
SEED_MODELS=0
SKIP_VERIFY=0
EXPORT_DIR=""
CASE_ID=""; ORG_ID=""; USER_ID=""; GEMINI_KEY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --instances)   INSTANCES="$2"; shift 2 ;;
    --no-build)    BUILD=0; shift ;;
    --seed-quality) SEED_QUALITY=1; shift ;;
    --seed-models) SEED_MODELS=1; shift ;;
    --gemini-key)  GEMINI_KEY="$2"; shift 2 ;;
    --export-dir)  EXPORT_DIR="$2"; shift 2 ;;
    --case-id)     CASE_ID="$2"; shift 2 ;;
    --org-id)      ORG_ID="$2"; shift 2 ;;
    --user-id)     USER_ID="$2"; shift 2 ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    *) echo "arg desconocido: $1"; exit 2 ;;
  esac
done

env_for() { case "$1" in site) echo ".env.advisorlegal" ;; develop) echo ".env.develop" ;; quality) echo ".env.quality" ;; *) echo "" ;; esac; }
log() { echo; echo "==> $*"; }

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker no está instalado"; exit 1; }

log "0/10 Comprobaciones previas"
missing=0
for inst in $INSTANCES; do E="$(env_for "$inst")"; [ -f "$E" ] || { echo "  FALTA $E"; missing=1; }; done
[ -f gcp-credentials.json ] || { echo "  FALTA gcp-credentials.json"; missing=1; }
[ "$missing" = "0" ] || { echo "ERROR: faltan archivos (cópialos y reintenta)"; exit 1; }
echo "  OK: .env de $INSTANCES y gcp-credentials.json presentes"

if [ "$BUILD" = "1" ]; then
  log "1/10 Construyendo imágenes (una sola imagen compartida)"
  docker compose --env-file .env.advisorlegal -f docker-compose.yml -f docker-compose.prod.yml build
fi

log "2/10 Red externa + infra COMPARTIDA (Postgres/Redis/ClamAV)"
docker network create judicial-net 2>/dev/null || true
docker compose -p judicial-infra --env-file .env.advisorlegal \
  -f infra/docker/docker-compose.shared-infra.yml up -d

log "3/10 Creando BD + roles por instancia"
for inst in $INSTANCES; do
  E="$(env_for "$inst")"; echo "  db_create $inst"
  ENV_FILE="$E" docker compose --env-file "$E" -f docker-compose.yml -f infra/docker/docker-compose.shared-app.yml \
    run --rm --no-deps api python /srv/scripts/db_create.py
done

log "4/10 Levantando las apps (+ migraciones Alembic)"
for inst in $INSTANCES; do
  E="$(env_for "$inst")"; echo "  app $inst"
  ENV_FILE="$E" bash scripts/deploy.sh --shared --no-proxy
done

log "5/10 Creando organización + administrador por instancia (seed_admin)"
for inst in $INSTANCES; do
  E="$(env_for "$inst")"; echo "  admin $inst"
  ENV_FILE="$E" bash scripts/seed_admin.sh
done

log "6/10 Semillas de demo (sólo dev/qa; site NUNCA)"
DOCKER=1 bash scripts/migrate_seeds.sh --seed .env.develop
if [ "$SEED_QUALITY" = "1" ]; then DOCKER=1 bash scripts/migrate_seeds.sh --seed .env.quality; fi

log "7/10 Proxy ÚNICO (Caddy) para los 3 dominios"
docker compose -p judicial-proxy -f docker-compose.shared-proxy.yml up -d

if [ -n "$EXPORT_DIR" ]; then
  log "8/10 Cargando el expediente en las 3 instancias"
  bash scripts/import_case_all.sh --export-dir "$EXPORT_DIR"
else
  log "8/10 Import del expediente OMITIDO (usa --export-dir <carpeta>)"
fi

if [ -n "$CASE_ID" ] && [ -n "$ORG_ID" ] && [ -n "$USER_ID" ]; then
  log "9/10 Post-despliegue de site (reindex embeddings + modelos Gemini)"
  ENV_FILE=.env.advisorlegal docker compose --env-file .env.advisorlegal exec -T api \
    python /srv/scripts/index_chunks_cli.py --case-id "$CASE_ID" --org-id "$ORG_ID" --user-id "$USER_ID"
  if [ "$SEED_MODELS" = "1" ] && [ -n "$GEMINI_KEY" ]; then
    ENV_FILE=.env.advisorlegal docker compose --env-file .env.advisorlegal exec -T api \
      python /srv/scripts/seed_ai_models.py --org-id "$ORG_ID" --user-id "$USER_ID" --api-key "$GEMINI_KEY"
  fi
else
  log "9/10 Post-despliegue OMITIDO (usa --case-id --org-id --user-id [--seed-models --gemini-key])"
fi

if [ "$SKIP_VERIFY" = "0" ]; then
  log "10/10 Verificación"
  bash scripts/verify_deploy.sh || true
fi

echo
echo "✅ Despliegue completo."
echo "   Dominios: advisorlegal.co · develop.advisorlegal.co · quality.advisorlegal.co"
echo "   Login: con BOOTSTRAP_ADMIN_EMAIL/PASSWORD de cada .env"
echo "   Nota: DNS (A) de los 3 dominios debe apuntar a la IP del VPS; puertos 80/443 abiertos."
