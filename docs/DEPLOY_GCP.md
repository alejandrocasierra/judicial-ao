# Desplegar en un VPS de GCP (con OpenCode)

Guía ordenada de cero a producción. **Lo lento es construir la imagen** (torch/whisper ≈ 7 GB, ~20–40 min
la primera vez), **no** los datos: la migración es un `pg_dump` + copiar `var/storage` y **no** va dentro de la
imagen. Sigue el orden y no tendrás que reconstruir por los datos.

---

## 0) Antes de empezar
- **VPS**: Ubuntu 22.04/24.04, **4+ vCPU, 8–16 GB RAM, 80–120 GB de disco** (la imagen `api` con CPU-torch pesa ~7 GB).
- **Dominio**: registro **DNS A** `advisorlegal.co` → **IP pública del VPS**.
- **Firewall GCP**: abrir **tcp:80 y tcp:443** (Caddy/TLS). **No** expongas 5432/6379/8000/3000.
- Repo: https://github.com/alejandrocasierra/judicial-ao (rama `main`).

## 1) Preparar el VPS
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git curl
sudo usermod -aG docker "$USER" && exec newgrp docker   # re-entra para usar docker sin sudo

# (recomendado) OpenCode instalado en el VPS, para operar/depurar desde el propio servidor:
curl -fsSL https://opencode.ai/install | bash
exec "$SHELL" -l
opencode            # autentícate con tu proveedor de modelo (Anthropic/OpenAI/…)

# (opcional) Google Cloud SDK, sólo si vas a bajar el volcado de un bucket GCS:
# https://cloud.google.com/sdk/docs/install
```

## 2) Traer el código
```bash
git clone https://github.com/alejandrocasierra/judicial-ao.git
cd judicial-ao
```
> Si ya clonaste antes: `git pull origin main` para tener los scripts y migraciones al día.

## 3) Configurar `.env`
```bash
cp .env.production.example .env
python3 scripts/gen_env.py --template .env.production.example --force --out .env   # rellena secretos
nano .env
```
Ajusta (el resto ya viene correcto para producción):
```dotenv
APP_ENV=production
PUBLIC_DOMAIN=advisorlegal.co
WEB_BASE_URL=https://advisorlegal.co
CORS_ALLOWED_ORIGINS=https://advisorlegal.co
API_PUBLIC_URL=/v1                 # mismo dominio tras Caddy → sin CORS
MCP_ALLOWED_HOSTS=advisorlegal.co
REGISTRY_IMAGE=                    # vacío = build en el VPS; o registry.gitlab.com/usuario/repo (recomendado)
# LLM por defecto. Para Gemini se usa el adaptador OpenAI-compatible:
LLM_PROVIDER=openai
LLM_API_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_MODEL=gemini-flash-latest
LLM_API_KEY=tu_api_key_de_gemini
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=gemini-embedding-001   # devuelve 1024 dims (coherente con EMBEDDING_DIMENSIONS)
EMBEDDING_API_KEY=tu_api_key_de_gemini
EMBEDDING_API_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
# Storage = el MISMO GCS de local (bucket compartido). La credencial gcp-credentials.json
# se monta en el contenedor; NO va al repo.
STORAGE_BACKEND=gcs
S3_ENDPOINT_URL=https://storage.googleapis.com
S3_BUCKET=welladvisor
S3_REGION=auto
S3_PREFIX=judicial-ai/dev            # para leer los objetos ya subidos desde local; usa "judicial-ai/prod" si haces export/import
S3_SSE=
GOOGLE_CLOUD_PROJECT=welladvisor    # OCR document_ai (opcional)
GOOGLE_DOCUMENT_AI_PROCESSOR_ID=16e9ace786ea8124
BOOTSTRAP_ADMIN_EMAIL=tucorreo@advisorlegal.co
BOOTSTRAP_ADMIN_PASSWORD=UnaClaveFuerte#2026
BOOTSTRAP_ADMIN_NAME=Administrador
```
> **Credencial GCP**: copia `gcp-credentials.json` (service account `ocrdocumentai@welladvisor`)
> junto al `docker-compose.yml` en el VPS (`scp gcp-credentials.json usuario@VPS:~/judicial-ao/`).
> El compose ya la monta en `/srv/gcp-credentials.json` con `GOOGLE_APPLICATION_CREDENTIALS`.
> Sirve tanto para GCS (storage / subida directa) como para Document AI.
> `EMBEDDING_DIMENSIONS=1024` ya está fijado y **debe** coincidir con los vectores migrados. Si cambias de
> modelo de embeddings, usa uno de 1024 dims o recrea la BD y reindexa.

## 4) Imágenes Docker — aquí está lo que tarda
Elige **una** opción. `scripts/deploy.sh` ya decide: hace `pull` si hay `REGISTRY_IMAGE`, si no `build`.

- **A. Registry (la más rápida en el VPS, recomendada)**
  Construye en tu equipo o en CI, publica y en el VPS solo se descarga:
  ```bash
  # en tu equipo (o CI):
  docker compose build api worker mcp web
  # publica a tu registry y en el VPS:
  #   REGISTRY_IMAGE=registry.gitlab.com/usuario/repo  en .env
  #   docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml pull
  ```

- **B. Construir en el VPS** (simple, pero ~20–40 min la primera vez; luego la caché lo hace rápido):
  ```bash
  docker compose build          # deja REGISTRY_IMAGE vacío
  ```
  Los rebuilds posteriores **no** re-descargan torch: sólo se invalidan las capas `COPY` del código.

- **C. Transferir la imagen ya construida (sin registry)**
  ```bash
  # en tu equipo:
  docker compose build api worker mcp web
  docker save judicial-ai/api judicial-ai/web -o judicial-ai-images.tar
  scp judicial-ai-images.tar USUARIO@IP_VPS:/home/USUARIO/
  # en el VPS:
  docker load -i judicial-ai-images.tar
  ```

> `api`, `worker` y `mcp` comparten la **misma** imagen (`api.Dockerfile`).

## 5) Levantar + administrador
```bash
bash scripts/deploy.sh        # imágenes + up + migraciones + healthcheck
bash scripts/seed_admin.sh    # crea SOLO el admin (+ org y agentes/skills), sin datos de demo
```

## 6) Migrar los datos (privado; NO van en la imagen)
```bash
# Opción 1: pasar el volcado por scp/rsync y en el VPS:
bash scripts/import_data.sh /home/USUARIO/judicial-<fecha>

# Opción 2: desde un bucket GCS:
bash scripts/fetch_and_import.sh gs://mi-bucket/judicial

# Opción 3: mover SÓLO un expediente a una instancia que ya tiene datos:
python scripts/import_case.py --in casos/<id> --org-id <uuid-org-destino>
```
Detalle y alternativas: **[`MIGRAR_DATOS.md`](MIGRAR_DATOS.md)**.
Recuerda: **nunca** subas el volcado ni `var/storage` a GitHub; `.gitignore` y `.dockerignore` ya los excluyen.

## 7) TLS, DNS y verificación
- **DNS**: `advisorlegal.co` (A) → IP del VPS.
- **Firewall**: sólo 80/443.
- Caddy emite el certificado TLS automáticamente (`docker-compose.prod.yml`).
- Verifica:
  - `https://advisorlegal.co` → login.
  - `https://advisorlegal.co/health` → `{"status":"ok"}`.
  - `docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml ps` → todo `healthy`.
  - Abre un PDF (original + OCR) y pregunta al chat (debe citar documento+página / video+minuto).

## 8) Varias instancias (site / develop / quality)
Tres despliegues sobre el mismo VPS, cada uno con su BD/roles, su índice de Redis y sus puertos.
Los `.env` ya están listos: `.env.advisorlegal` (site), `.env.develop` y `.env.quality`.

| Instancia | `.env` | POSTGRES_DB | Rol dueño | Rol app | Redis | API · MCP | Dominio |
|---|---|---|---|---|---|---|---|
| **site** | `.env.advisorlegal` | `judicial` | `judicial_owner` | `judicial_app` | /0 /1 /2 | 8000 · 8100 | advisorlegal.co |
| **develop** | `.env.develop` | `judicial_develop` | `judicial_owner_dev` | `judicial_app_dev` | /3 /4 /5 | 8001 · 8101 | develop.advisorlegal.co |
| **quality** | `.env.quality` | `judicial_quality` | `judicial_owner_qa` | `judicial_app_qa` | /6 /7 /8 | 8002 · 8102 | quality.advisorlegal.co |

Cada instancia con su propio proyecto Compose y `--env-file`:
```bash
docker compose -p judicial-site    --env-file .env.advisorlegal -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose -p judicial-develop --env-file .env.develop      -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose -p judicial-quality --env-file .env.quality      -f docker-compose.yml -f docker-compose.prod.yml up -d
```
> Comparten el **bucket GCS** `welladvisor`. **site y develop usan `judicial-ai/dev`** (para que site
> lea directamente los objetos ya subidos desde local); quality usa `judicial-ai/quality`.
> Si quieres separar site, cambia su `S3_PREFIX` a `judicial-ai/prod` (y haz export/import del caso).
> Si prefieres **un Postgres/Redis compartido** en vez de uno por instancia, apunta `POSTGRES_HOST`
> y `REDIS_URL_DOCKER`/`CELERY_*_DOCKER` a ese servicio (red Docker externa) y conserva los
> índices de Redis (`/0-2`, `/3-5`, `/6-8`) y el `POSTGRES_DB`.

Migraciones y semillas por instancia:
```bash
bash scripts/migrate_seeds.sh .env.develop .env.quality      # develop siembra; quality (staging) no
bash scripts/migrate_seeds.sh --seed .env.quality            # forzar semilla en quality
bash scripts/migrate_seeds.sh --no-seed .env.advisorlegal    # site: sólo migrar
```

### 8.1 Modo compartido (un Postgres + Redis + ClamAV) — opcional
En vez de 3 Postgres/Redis (uno por instancia), se puede correr **uno solo** y que las 3 instancias apunten a él. Ficheros incluidos: `infra/docker/docker-compose.shared-infra.yml` y `infra/docker/docker-compose.shared-app.yml`.
> Requisito: el **mismo** `POSTGRES_SUPERUSER_PASSWORD` en los 3 `.env` (ya viene unificado).

```bash
# 1) Red externa + infra compartida (una sola vez)
docker network create judicial-net 2>/dev/null || true
docker compose -p judicial-infra --env-file .env.advisorlegal \
  -f infra/docker/docker-compose.shared-infra.yml up -d

# 2) Crear BD + roles de cada instancia (idempotente) desde un contenedor de la red
docker compose -p judicial-site    --env-file .env.advisorlegal -f docker-compose.yml -f infra/docker/docker-compose.shared-app.yml run --rm --no-deps api python /srv/scripts/db_create.py
docker compose -p judicial-develop --env-file .env.develop      -f docker-compose.yml -f infra/docker/docker-compose.shared-app.yml run --rm --no-deps api python /srv/scripts/db_create.py
docker compose -p judicial-quality --env-file .env.quality      -f docker-compose.yml -f infra/docker/docker-compose.shared-app.yml run --rm --no-deps api python /srv/scripts/db_create.py

# 3) Arrancar las apps (sin Postgres/Redis locales)
docker compose -p judicial-site    --env-file .env.advisorlegal -f docker-compose.yml -f infra/docker/docker-compose.shared-app.yml up -d --no-deps api worker mcp web
docker compose -p judicial-develop --env-file .env.develop      -f docker-compose.yml -f infra/docker/docker-compose.shared-app.yml up -d --no-deps api worker mcp web
docker compose -p judicial-quality --env-file .env.quality      -f docker-compose.yml -f infra/docker/docker-compose.shared-app.yml up -d --no-deps api worker mcp web

# 4) Migraciones + semillas (por instancia)
bash scripts/migrate_seeds.sh .env.develop .env.quality
bash scripts/migrate_seeds.sh --no-seed .env.advisorlegal
```
> Los **datos** deben estar en las 3: importa el expediente/seeds en cada instancia
> (`import_case.py` copia los bytes al prefijo de cada una). Si prefieres no complicarte,
> usa el modo **aislado** de §8 (cada instancia con su Postgres/Redis).

## 9) Post-despliegue: embeddings e IA (Gemini)
Tras importar los datos, **reindexa el caso** para que los vectores usen el modelo real (Gemini):
```bash
docker compose -p judicial-site --env-file .env.advisorlegal exec -T api \
  python /srv/scripts/index_chunks_cli.py --case-id <uuid-caso> --org-id <uuid-org> --user-id <uuid-user>
```
Da de alta los **modelos de Gemini** en la organización (idempotente; aparecen en el selector del chat):
```bash
docker compose -p judicial-site --env-file .env.advisorlegal exec -T api \
  python /srv/scripts/seed_ai_models.py --org-id <uuid-org> --user-id <uuid-admin> --api-key <GEMINI_API_KEY>
```

## Con OpenCode en el VPS
Una vez instalado (`curl -fsSL https://opencode.ai/install | bash`), puedes usarlo dentro del servidor:
```bash
cd judicial-ao
opencode        # pídele, p.ej.: "revisa docker compose ps y arregla lo que esté unhealthy"
```
Útil para: leer logs (`docker compose logs api`), verificar migraciones, revisar el `.env` (sin exponer secretos)
y diagnosticar el reverse proxy.

## Notas prácticas
- **No hornees datos en la imagen**: ni el caso real, ni backups. `var/`, `backup/`, `.env*` y el expediente
  de 9 GB están en `.dockerignore`.
- **No recompiles por datos**: cambiar datos no toca la imagen.
- **Recursos**: el worker carga torch/whisper; dale RAM suficiente. Con GPU, `ASR_DEVICE=cuda`.
- **Reinicios**: `docker compose restart web` tras cambiar `API_PUBLIC_URL`; `deploy.sh` para el resto.
