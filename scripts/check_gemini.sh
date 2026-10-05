#!/usr/bin/env bash
# Prueba de humo de Gemini: listar modelos + chat + embeddings, usando la key del .env.
#
# Uso:
#   bash scripts/check_gemini.sh                 # usa .env.advisorlegal
#   bash scripts/check_gemini.sh .env.develop
set -uo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${1:-.env.advisorlegal}"
[ -f "$ENV_FILE" ] || { echo "ERROR: no existe $ENV_FILE"; exit 2; }

read_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' | tr -d '"' | tr -d '\r'; }
KEY="$(read_env LLM_API_KEY)"; [ -z "$KEY" ] && KEY="$(read_env EMBEDDING_API_KEY)"
MODEL="$(read_env LLM_MODEL)"; MODEL="${MODEL:-gemini-flash-latest}"
EMB="$(read_env EMBEDDING_MODEL)"; EMB="${EMB:-gemini-embedding-001}"
[ -n "$KEY" ] || { echo "ERROR: sin LLM_API_KEY/EMBEDDING_API_KEY en $ENV_FILE"; exit 2; }

BASE="https://generativelanguage.googleapis.com/v1beta"
pass=0; fail=0
ok() { echo "  ✅ $1"; pass=$((pass + 1)); }
ko() { echo "  ❌ $1"; fail=$((fail + 1)); }

echo "== Gemini ($ENV_FILE) =="

code="$(curl -s -o /tmp/gem_models.json -w '%{http_code}' -m 30 "$BASE/models?pageSize=3" -H "x-goog-api-key: $KEY")"
if [ "$code" = "200" ]; then ok "listar modelos (HTTP $code)"; else ko "listar modelos (HTTP $code)"; fi

code="$(curl -s -o /tmp/gem_chat.json -w '%{http_code}' -m 60 -X POST "$BASE/openai/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"di ok\"}],\"max_tokens\":20}")"
if [ "$code" = "200" ]; then ok "chat ($MODEL)"; else ko "chat ($MODEL): $(head -c 180 /tmp/gem_chat.json)"; fi

code="$(curl -s -o /tmp/gem_emb.json -w '%{http_code}' -m 60 -X POST "$BASE/openai/embeddings" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"$EMB\",\"input\":[\"hola\"],\"dimensions\":1024}")"
if [ "$code" = "200" ]; then
  dim=""
  if command -v python3 >/dev/null 2>&1; then
    dim="$(python3 -c "import json;print(len(json.load(open('/tmp/gem_emb.json'))['data'][0]['embedding']))" 2>/dev/null || true)"
  fi
  ok "embeddings ($EMB${dim:+ -> $dim dims})"
else
  ko "embeddings ($EMB): $(head -c 180 /tmp/gem_emb.json)"
fi

echo
echo "Resumen: $pass OK, $fail fallos"
[ "$fail" -eq 0 ]
