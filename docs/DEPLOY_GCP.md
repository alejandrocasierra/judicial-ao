# Desplegar en un VPS de GCP (Docker + GitLab)

Guía para subir el proyecto a GitLab, construir la imagen Docker y correrla en un VPS
de GCP sin que queden URLs de `localhost`.

## Arquitectura
```
Navegador
   │  https://app.tudominio.com
   ▼
[Caddy / TLS]  (proxy)
   ├─ /v1/*, /health ──► api:8000   (FastAPI)
   └─ el resto       ──► web:3000   (Next.js)
[api] [worker] [mcp] [postgres] [redis]   (solo red interna de Docker)
```

## Punto clave: por qué antes salía `localhost`
`NEXT_PUBLIC_API_URL` se **hornea en tiempo de build** de Next.js; por eso el navegador
seguía llamando a `http://localhost:8000`. Ahora la URL del API se inyecta en **runtime**:

- El layout (servidor) escribe `window.__API_URL__` a partir de la env **`API_PUBLIC_URL`**.
- `apps/web/src/lib/api.ts` la usa; si está vacía, cae a `http://localhost:8000/v1`.
- Así, la **misma imagen** sirve en localhost o en GCP; solo cambias la env y reinicias el
  contenedor `web`.

## 1) Subir a GitLab
Desde la raíz del repo:
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://gitlab.com/<usuario>/<repo>.git
git push -u origin main
```
`.gitignore` ya excluye **`.env`, `.env.*`, `gcp-credentials.json`, `var/`**. Verifica antes
de subir que no se cuelen secretos:
```bash
git status --porcelain | grep -E "\.env|gcp-credentials" || echo "OK: sin secretos"
```
> Nunca subas `.env` ni `gcp-credentials.json`. En GitLab los guardas como variables de CI o
> los creas directo en el VPS.

## 2) Construir / compartir la imagen
### Opción A — GitLab Container Registry (recomendada)
`.gitlab-ci.yml` ya construye y publica `…/api` y `…/web` en el Registry al hacer push a `main`.
En el VPS solo haces `docker compose pull`.

### Opción B — Pasar la imagen como archivo (`docker save`/`load`)
Si prefieres entregar un tar sin registry:
```bash
# En tu máquina
docker compose build api worker mcp web
docker save judicial-ai-api judicial-ai-web -o judicial-ai-images.tar
# Entregas judicial-ai-images.tar + el repo (o el compose) a la otra persona
# En el VPS
docker load -i judicial-ai-images.tar
```
> `api`, `worker` y `mcp` comparten la misma imagen (`api.Dockerfile`).

## 3) Configurar `.env` en el VPS (quitar localhost)
Copia `.env.example` a `.env` y ajusta para producción. Las variables que **deben** dejar de
ser `localhost`:

| Variable | Valor en GCP |
|---|---|
| `APP_ENV` | `production` |
| `PUBLIC_DOMAIN` | `app.tudominio.com` (DNS → IP del VPS) |
| `API_PUBLIC_URL` | `/v1` (mismo dominio, recomendado) o `https://api.tudominio.com/v1` |
| `CORS_ALLOWED_ORIGINS` | `https://app.tudominio.com` (si usas subdominios; vacío/no aplica si es mismo origen) |
| `WEB_BASE_URL` | `https://app.tudominio.com` |
| `API_HOST` | `0.0.0.0` (el compose ya lo fuerza en contenedores) |
| `MCP_ALLOWED_HOSTS` | `app.tudominio.com:443` (o el host público del MCP) |
| `POSTGRES_HOST` / `REDIS_URL` / `CELERY_*` | los inyecta `docker-compose.yml` (`postgres`, `redis`); no toques |
| `JWT_SECRET`, `*_PASSWORD`, `S3_*` | valores reales y fuertes (nunca los de ejemplo) |
| `LLM_PROVIDER`/`LLM_MODEL`/`LLM_API_KEY` | tu proveedor real (en `production` no puede ser `fake`) |
| `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`/`EMBEDDING_API_KEY` | real (el `fake` es para tests) |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/srv/gcp-credentials.json` (lo monta el compose) |
| `PUBLIC_DOMAIN` (Caddy) | el mismo que arriba |

Genera secretos con `python3 scripts/gen_env.py` (o edítalos a mano) — no reutilices los de `.env.example`.

## 4) Levantar en el VPS
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Migraciones (desde el host, apuntando al Postgres del compose) y datos iniciales
bash scripts/db_migrate.sh up        # o: docker compose exec api alembic -c alembic.ini upgrade head
# Admin de acceso rápido (opcional, definido en .env con BOOTSTRAP_ADMIN_*)
```
Comprobaciones:
- `https://app.tudominio.com` → login.
- `https://app.tudominio.com/health` → `{"status":"ok"}`.
- El navegador debe llamar a `/v1/...` (mismo dominio). Si ves errores de red, revisa
  `API_PUBLIC_URL` y reinicia `web`.

## 5) Firewall GCP
Abre solo **80 y 443** (Caddy). PostgreSQL/Redis/API/web quedan en la red interna de Docker.
```bash
gcloud compute firewall-rules create allow-http-https \
  --allow tcp:80,tcp:443 --target-tags=http-server,https-server
```
No expongas 5432, 6379, 8000 ni 3000 a Internet.

## 6) Notas
- **TLS**: Caddy lo gestiona solo (Let's Encrypt) si `PUBLIC_DOMAIN` resuelve al VPS y 80/443
  están abiertos. Si aún no tienes dominio, puedes probar por IP con `PUBLIC_DOMAIN=:80` y
  `API_PUBLIC_URL=/v1` (HTTP, sin TLS).
- **OCR/ASR pesados**: `worker` carga torch/whisper; dale RAM/CPU suficientes. Si necesitas GPU,
  usa una VM con GPU y ajusta `ASR_DEVICE=cuda`.
- **Almacenamiento**: por defecto `local` (volumen `storage`). Para GCS/S3, usa
  `STORAGE_BACKEND=s3` + `S3_ENDPOINT_URL=https://storage.googleapis.com` + claves HMAC.
- **No regeneres** `.env` con valores de ejemplo en producción: usa secretos propios.
