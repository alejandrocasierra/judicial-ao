# Judicial AI Platform

**ES** · Plataforma *evidence-first* que convierte expedientes judiciales multimodales (PDF, imágenes, audio y video)
en un **Case Knowledge Package (CKP)** verificable: cada afirmación queda anclada a **documento + página** o a
**media + timestamp**. · **EN** · Evidence-first platform that turns multimodal court case files into verifiable
knowledge, with every statement anchored to document + page or media + timestamp.

> Los originales (PDF/video) son la **fuente de verdad**; OCR/ASR, chunks, embeddings y grafo son derivados y trazables.

---

## Español

### Arquitectura
```
        Expediente (PDF / video / audio / imagen)
                        │
             ┌──────────┴──────────┐
             ▼                     ▼
         OCR (páginas)        ASR + diarización
             │                     │
             └──────────┬──────────┘
                        ▼
        Texto estructurado (Markdown + JSON)
                        │
        chunking ──► embeddings (pgvector) ──► búsqueda híbrida (FTS + vector + RRF)
                        │
                        └────► Knowledge Graph (nodos/aristas) ──► Agente IA (ReAct) ──► respuesta citada
```

**Componentes**
- **API (FastAPI)** `apps/api`: REST `/v1`, RLS multi-tenant, OCR/ASR, RAG, grafo, export CKP.
- **Worker (Celery)**: pipelines OCR/ASR, extracción legal, indexación, grafo.
- **MCP** `apps/mcp_server`: tools del Chat IA.
- **Web (Next.js)** `apps/web`: dashboard.
- **Postgres + pgvector** (datos + vectores + FTS) y **Redis** (broker, rate limit).

**Estructura**: `apps/{api,mcp_server,web}` · `packages/{i18n,prompts,schemas}` · `config/` · `infra/docker/` ·
`scripts/` · `tests/`.

### Inicio rápido (local)
| Entorno | Comando |
|---|---|
| Claude Code en la web | Automático (hook `SessionStart`). Si falla: `bash scripts/setup_cloud_session.sh` |
| Linux con PostgreSQL 16 + pgvector | `bash scripts/bootstrap.sh` |
| Linux con Docker | `python3 scripts/gen_env.py && bash scripts/bootstrap.sh --docker` |

Después:
```bash
bash scripts/dev_server.sh     # API en http://127.0.0.1:8000
bash scripts/run_tests.sh      # unit · integration · security · behavior · static
```
Web en http://localhost:3000 · API en http://localhost:8000 (`/docs` en desarrollo).
Cuentas de prueba: `docs/TEST_ACCOUNTS.md` (dominio `example.test`, contraseña `SEED_DEFAULT_PASSWORD`).

### Variables de entorno
Toda la configuración vive en variables de entorno (referencia anotada en `.env.example`; nunca versiones el `.env`).
`scripts/gen_env.py [--template <plantilla>]` genera el `.env` rellenando los `__GENERATE__` con secretos.

| Variable | Para qué |
|---|---|
| `APP_ENV`, `API_HOST`, `API_PORT`, `API_PREFIX` | entorno y puerto de la API (`/v1`) |
| `PUBLIC_DOMAIN`, `API_PUBLIC_URL`, `CORS_ALLOWED_ORIGINS`, `WEB_BASE_URL` | dominios públicos (producción) |
| `POSTGRES_*`, `DB_APP_USER`, `DB_OWNER_USER`, `DB_*_PASSWORD` | base de datos y roles (RLS) |
| `JWT_SECRET`, `ACCESS/REFRESH_TOKEN_TTL_*`, `*_RATE_LIMIT_*` | autenticación y límites |
| `LLM_PROVIDER/LLM_MODEL/LLM_API_KEY`, `OPENAI_/DEEPSEEK_/KIMI_/GEMINI_BASE_URL` | modelos de IA |
| `EMBEDDING_PROVIDER/EMBEDDING_MODEL/EMBEDDING_API_KEY` | embeddings (pgvector) |
| `OCR_PROVIDER`, `ASR_PROVIDER/ASR_MODEL`, `GOOGLE_DOCUMENT_AI_*` | OCR/ASR |
| `STORAGE_BACKEND`/`S3_*` | almacenamiento de originales (local/S3/GCS) |
| `REGISTRY_IMAGE`, `IMAGE_TAG` | despliegue de imágenes Docker |

Para usar un modelo real: `LLM_PROVIDER=anthropic` + `LLM_MODEL` + `LLM_API_KEY` en `.env`.

### Despliegue en producción (VPS / GCP)
Guía completa: **[`docs/DEPLOY_GCP.md`](docs/DEPLOY_GCP.md)**.
```bash
cp .env.production.example .env
python3 scripts/gen_env.py --template .env.production.example --force --out .env
nano .env                    # PUBLIC_DOMAIN, API_PUBLIC_URL, LLM/EMBEDDING_API_KEY, REGISTRY_IMAGE

bash scripts/deploy.sh       # imágenes + up + migraciones + healthcheck
bash scripts/seed_admin.sh   # crea SOLO el admin (+ org y agentes/skills), sin datos de demo
```
El reverse proxy (Caddy) y el TLS se activan con `docker-compose.prod.yml`. Abre solo **80/443** en el firewall.
En el VPS, coloca `gcp-credentials.json` si usas Document AI (lo monta el compose).

### Cargar datos (procesos, modelos, agentes, skills)
- **Procesos / expedientes**: créalos en el dashboard y sube archivos, o importa en bloque:
  ```bash
  python scripts/import_expediente.py --case-id <uuid> --org-id <uuid> --user-id <uuid> --folder /ruta/expediente
  ```
- **Modelos IA**: dashboard → **Modelos IA** → Nuevo modelo. Pega la API key y el catálogo se consulta
  **en vivo** al proveedor (trae todos los modelos del proveedor, incluidos los nuevos).
- **Agentes y skills**: los de sistema se crean **automáticamente** por organización (y `seed_admin.sh` los
  garantiza). Los propios se crean/editan en el dashboard.
- **Chat IA**: el agente lee documentos (incluido su **Markdown** estructurado), busca por minuto en audiencias,
  agrega personas por rol y **aplica correcciones** de OCR/ASR (con `reviews`, reindexado de pgvector y grafo).

### Exportar el expediente (CKP)
En el detalle del proceso, botón **Exportar CKP**: ZIP con `manifest.json`, `case.json`, JSONL por entidad,
`documents/markdown/*.md`, `chunks/chunks.jsonl`, grafo y auditoría.

### Documentación
- `CLAUDE.md`: reglas del repo y guía para agentes.
- `docs/SSD_ADDENDUM.md`: decisiones, modelo de datos, RBAC, API, estados y amenazas.
- `docs/BACKLOG.md`: hecho y pendiente · `docs/TEST_PLAN.md` · `docs/TEST_CATALOG.md`.

### Seguridad
Multi-tenant por **RLS**; **evidencia inmutable** (sin borrado por API, `audit_logs`/`reviews` sólo insertan);
**IA con evidencia** (sin evidencia no se llama al modelo); **secretos** fuera del repo.

---

## English

### Architecture
Evidence-first pipeline: **OCR/ASR → structured text (Markdown + JSON) → chunks → embeddings (pgvector) +
hybrid search (FTS + vector + RRF) + knowledge graph → AI agent → cited answer.** Originals (PDF/video) are the
source of truth; everything else is derived and traceable.

Components: **API (FastAPI)**, **worker (Celery)**, **MCP server**, **web (Next.js)**, **Postgres + pgvector**,
**Redis**.

### Quick start (local)
| Environment | Command |
|---|---|
| Claude Code on the web | Automatic (`SessionStart` hook). If it fails: `bash scripts/setup_cloud_session.sh` |
| Linux with PostgreSQL 16 + pgvector | `bash scripts/bootstrap.sh` |
| Linux with Docker | `python3 scripts/gen_env.py && bash scripts/bootstrap.sh --docker` |

Then `bash scripts/dev_server.sh` (API on http://127.0.0.1:8000) and `bash scripts/run_tests.sh`.
Web on http://localhost:3000. Test accounts: `docs/TEST_ACCOUNTS.md`.

### Configuration
All configuration is environment-driven (annotated reference in `.env.example`; never commit `.env`).
`scripts/gen_env.py [--template <file>]` fills the `__GENERATE__` placeholders with random secrets.

### Production (VPS / GCP)
See **[`docs/DEPLOY_GCP.md`](docs/DEPLOY_GCP.md)**: `cp .env.production.example .env` → `gen_env.py --template`
→ `bash scripts/deploy.sh` → `bash scripts/seed_admin.sh`.

### Loading data
Create **processes/cases** in the dashboard or bulk-import with `scripts/import_expediente.py`; add **AI models**
in the dashboard (the catalog is fetched live from each provider, so new models appear automatically);
**system agents/skills** are created automatically per organization. Export the case as a **CKP** (manifest +
JSONL + Markdown + chunks + graph) from the process page.

### Security
Multi-tenant **RLS**, **immutable evidence**, **grounded AI** (no model call without evidence), secrets never committed.
