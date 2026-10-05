#!/usr/bin/env bash
# Verificación post-deploy: API/Web y TLS de los 3 dominios (o salud local por puertos).
#
# Uso:
#   bash scripts/verify_deploy.sh
#   bash scripts/verify_deploy.sh --local
#   bash scripts/verify_deploy.sh --domains "advisorlegal.co develop.advisorlegal.co quality.advisorlegal.co"
#   bash scripts/verify_deploy.sh --ports "8000 8001 8002"
set -uo pipefail

DOMAINS=(advisorlegal.co develop.advisorlegal.co quality.advisorlegal.co)
PORTS=(8000 8001 8002)
MODE=domain

while [ $# -gt 0 ]; do
  case "$1" in
    --domains) read -r -a DOMAINS <<< "$2"; shift 2 ;;
    --ports)   read -r -a PORTS   <<< "$2"; shift 2 ;;
    --local)   MODE=local; shift ;;
    *) echo "arg desconocido: $1"; exit 2 ;;
  esac
done

pass=0; fail=0
chk() { # url
  local url="$1" code
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$url" 2>/dev/null)"; code="${code:-000}"
  if [ "$code" = "200" ]; then echo "  ✅ $url -> 200"; pass=$((pass + 1))
  else echo "  ❌ $url -> $code"; fail=$((fail + 1)); fi
}

echo "== Contenedores por proyecto =="
for p in judicial-infra judicial-proxy judicial-site judicial-develop judicial-quality; do
  n="$(docker ps --filter "label=com.docker.compose.project=$p" -q 2>/dev/null | wc -l | tr -d ' ')"
  echo "  $p: ${n} contenedores"
done

if [ "$MODE" = "local" ]; then
  echo "== API local (health por puerto) =="
  for port in "${PORTS[@]}"; do chk "http://127.0.0.1:${port}/health"; done
else
  echo "== API por dominio (/health) =="
  for d in "${DOMAINS[@]}"; do chk "https://${d}/health"; done
  echo "== Web por dominio (/) =="
  for d in "${DOMAINS[@]}"; do chk "https://${d}/"; done
  echo "== TLS (fecha de expiración) =="
  for d in "${DOMAINS[@]}"; do
    end="$(echo | openssl s_client -connect "${d}:443" -servername "$d" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null || true)"
    if [ -n "$end" ]; then echo "  ✅ $d ${end#notAfter=}"; else echo "  ⚠️  $d sin TLS legible (¿certificado aún no emitido?)"; fi
  done
fi

echo
echo "Resumen: ${pass} OK, ${fail} fallos"
[ "$fail" -eq 0 ]
