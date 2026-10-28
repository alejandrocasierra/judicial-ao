#!/usr/bin/env bash
# Limpieza de disco de Docker: caché de build + capas huérfanas.
#
# Por qué: BuildKit guarda cada capa construida para acelerar el rebuild y NO la
# libera nunca. Con api.Dockerfile (torch, docling, pyannote...) cada build deja
# varios GB; en un VPS que redespliega en cada push el disco acaba lleno
# ("no space left on device") y el deploy muere a mitad.
#
# Qué borra y qué NO:
#   - SÍ: caché de build, recortada a BUILD_CACHE_KEEP (2GB por defecto)
#   - SÍ: imágenes <none>, la versión anterior de lo que se acaba de reconstruir
#   - NO: volúmenes (pgdata, storage, minio, caddy), contenedores ni imágenes en uso
#
# Uso:
#   bash scripts/docker_cleanup.sh                 # recorte normal (lo que hace el deploy)
#   BUILD_CACHE_KEEP=5GB bash scripts/docker_cleanup.sh
#   bash scripts/docker_cleanup.sh --all-cache     # borra TODA la caché (rebuild lento)
#   bash scripts/docker_cleanup.sh --dry-run       # sólo informa
set -euo pipefail

KEEP="${BUILD_CACHE_KEEP:-2GB}"
MODE="trim"
DRY=0
for a in "$@"; do
  case "$a" in
    --all-cache|--no-cache) MODE="all" ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "argumento desconocido: $a" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker no está instalado"; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: el daemon de Docker no responde"; exit 1; }

resumen() {
  docker system df --format '{{.Type}}\t{{.Size}}\t(recuperable: {{.Reclaimable}})' 2>/dev/null | sed 's/^/    /' || true
}

echo "==> Disco de Docker antes:"
resumen

if [ "$MODE" = "all" ]; then
  echo "==> Borrando TODA la caché de build"
  args=(builder prune -f -a)
else
  echo "==> Recortando caché de build a $KEEP"
  # Docker >= 27 renombró --keep-storage a --max-used-space (el viejo sigue
  # aceptándose pero avisa de deprecación).
  if docker builder prune --help 2>/dev/null | grep -q -- '--max-used-space'; then
    args=(builder prune -f --max-used-space "$KEEP")
  else
    args=(builder prune -f --keep-storage "$KEEP")
  fi
fi

if [ "$DRY" = "1" ]; then
  echo "    docker ${args[*]}  (no ejecutado)"
  echo "    docker image prune -f            (no ejecutado)"
else
  docker "${args[@]}"
  # <none> = versión anterior de cada imagen reetiquetada en el rebuild. Sólo
  # borra lo que ya no tiene tag ni contenedor: nada en uso se toca.
  echo "==> Borrando imágenes huérfanas (<none>)"
  docker image prune -f
fi

echo "==> Disco de Docker después:"
resumen
