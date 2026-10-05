# Coordinación con Néstor (despliegue y pendientes §10)

Estado compartido para no perder el hilo entre el equipo y el VPS/GPUs.

Repo: https://github.com/alejandrocasierra/judicial-ao
Ramas actualizadas (todas al mismo commit): `main`, `develop`, `develop01`, `develop07`, `quality`, `Site`.

## 1) Bloqueo SSH de VPS5 (NO es nuestro)
**Síntoma**: VPS5 rechaza SSH tras >6 conexiones en 30 s; 10 GPUs pidiendo LoRAs a la vez → 146 fallos, 0 completados.
**Causa**: límite de `sshd`/firewall (`MaxStartups` / rate-limit), no del pipeline.
**Acciones**:
- Infra: subir `MaxStartups`/`MaxSessions` o quitar el rate-limit de conexiones SSH.
- Nuestro lado: **serializar** las descargas de LoRA (cola + reintentos con backoff) y reutilizar conexión (`ControlMaster`).
- Mejor: bajar cada LoRA **una vez** a un almacén compartido (bucket/NFS) y que las GPUs lo tomen de ahí, no por SSH.

## 2) Pendientes §10 y estado
| Punto | Estado | Cómo se resuelve |
|---|---|---|
| **Storage de producción** | ✅ | **GCS** bucket `welladvisor`. **site y develop leen `judicial-ai/dev`** (site ve directo los objetos subidos desde local); quality usa `judicial-ai/quality`. Credencial `gcp-credentials.json` (SA `ocrdocumentai@welladvisor`). La subida directa (presign) usa esa misma credencial. |
| **Migraciones + semillas de las 3 BD** | ✅ | Ver tabla en §5.1. `bash scripts/migrate_seeds.sh .env.develop .env.quality` (quality/staging sólo con `--seed`; `site` nunca siembra). |
| **API keys de "site"** | ✅ | Gemini (LLM **y** embeddings) ya configurado en los `.env`; ver §5.2. |
| **Node.js para el frontend** | ✅ no hace falta | El frontend se despliega como **imagen Docker** (build con `node:22` dentro). |
| **Límite de 100 MB de Cloudflare** | ✅ implementado | **Subida directa a GCS** por URL prefirmada (`/uploads/presign` + `/uploads/complete`). |

## 3) Qué se agregó al repo (ya en `main`)
- `scripts/migrate_seeds.sh` — migra + siembra varias BD (`--seed` / `--no-seed`).
- `scripts/seed_ai_models.py` — **alta de modelos Gemini** por organización (idempotente).
- **Subida directa** de archivos grandes a GCS/S3 (presign + complete) y frontend para > 90 MB.
- **3 `.env` listos** (gitignored, se entregan aparte): `.env.advisorlegal` (site), `.env.develop`, `.env.quality`.
- `docker-compose.yml` — soporta `WEB_PORT`, `REDIS_PORT` y `REDIS_URL_DOCKER`/`CELERY_*_DOCKER` por instancia.
- `requirements/base.txt` — `openai==1.55.3` (compatible con httpx 0.28; **necesario** para los embeddings de Gemini).
- `docs/DEPLOY_GCP.md` §8 (multi-instancia), §8.1 (modo compartido) y §9 (reindexado + alta de modelos).
- `infra/docker/docker-compose.shared-infra.yml` + `docker-compose.shared-app.yml` — **modo compartido** (un Postgres/Redis/ClamAV para las 3 instancias).
- `scripts/db_create.py` — ahora corre dentro del contenedor (crea BD + roles de cada instancia).

## 4) Cómo bajar la última versión (para cualquiera)
```bash
git fetch origin
git checkout main && git pull origin main     # o la rama que corresponda (develop/quality/Site)
```
> Todas las ramas (`main`, `develop`, `develop01`, `develop07`, `quality`, `Site`) apuntan al **mismo commit**.

## 5) Config acordada

### 5.1 Las 3 instancias
| Instancia | `.env` | POSTGRES_DB | Rol dueño | Rol app | Redis | API · MCP | Dominio |
|---|---|---|---|---|---|---|---|
| **site** | `.env.advisorlegal` | `judicial` | `judicial_owner` | `judicial_app` | /0 /1 /2 | 8000 · 8100 | advisorlegal.co |
| **develop** | `.env.develop` | `judicial_develop` | `judicial_owner_dev` | `judicial_app_dev` | /3 /4 /5 | 8001 · 8101 | develop.advisorlegal.co |
| **quality** | `.env.quality` | `judicial_quality` | `judicial_owner_qa` | `judicial_app_qa` | /6 /7 /8 | 8002 · 8102 | quality.advisorlegal.co |

- **Semillas**: `develop` (`APP_ENV=development`) siembra; `quality` (`staging`) sólo con `--seed`; `site` (`production`) nunca.
- **A confirmar con Néstor**: ¿un Postgres/Redis **compartido** entre las 3, o uno por instancia? Los `.env` soportan ambos.

### 5.2 API keys / credenciales
| Necesidad | Estado |
|---|---|
| **LLM** | ✅ Gemini (`gemini-flash-latest`, endpoint OpenAI-compatible) |
| **Embeddings** (1024 dims) | ✅ `gemini-embedding-001` |
| **OCR Document AI** | ✅ `gcp-credentials.json` (proyecto `welladvisor`) |
| **Storage** | ✅ GCS `welladvisor` |
| **ASR / diarización** (opcional) | ✅ `HF_TOKEN` |
| **Correo** (opcional) | ⏳ si se quiere SMTP |

> ⚠️ Las API keys compartidas por chat (Gemini/OpenAI/Kimi) **deben rotarse**. Se copian una sola vez al `.env` del servidor; **nunca** al repo.
