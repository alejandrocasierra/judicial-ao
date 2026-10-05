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
| **Storage de producción** | ✅ confirmado | **GCS**: bucket `welladvisor`, prefijo `judicial-ai/prod` (en local se usa `judicial-ai/dev`). Credencial: `gcp-credentials.json` (service account `ocrdocumentai@welladvisor.iam.gserviceaccount.com`). La subida directa (presign) usa esa misma credencial. |
| **Migraciones + semillas de las 3 BD** | ⏳ falta info | `bash scripts/migrate_seeds.sh .env.dev .env.staging .env.prod`. **Falta**: hosts/nombres de las 3 BD y si las semillas son sintéticas o reales (ver §5.1). |
| **API keys de \"site\"** | ⏳ falta provisión | Ver matriz §5.2. Van en el `.env` del servidor, **nunca** al repo. |
| **Node.js para el frontend** | ✅ no hace falta | El frontend se despliega como **imagen Docker** (build con `node:22` dentro). Sólo se necesita Node en el host si se compila fuera de Docker. |
| **Límite de 100 MB de Cloudflare (videos grandes)** | ✅ implementado | **Subida directa a GCS** por URL prefirmada (`/uploads/presign` + `/uploads/complete`), sin pasar por el proxy de Cloudflare. Alternativa/simple: subdominio **solo-DNS (gris)** para subidas. |

## 3) Qué se agregó al repo (ya en `main`)
- `scripts/migrate_seeds.sh` — migra + siembra varias BD en un comando.
- **Subida directa de archivos grandes** a GCS/S3:
  - Backend: `POST /cases/{case_id}/uploads/presign` (devuelve URL prefirmada; si el storage es local, `mode: local`) y
    `POST /cases/{case_id}/uploads/complete` (registra y encola el procesamiento).
  - Frontend: los archivos **> 90 MB** se suben directo al storage; los pequeños siguen por multipart.
- `docs/DEPLOY_GCP.md` — guía de despliegue (con OpenCode en el VPS).
- `docs/MIGRAR_DATOS.md` — migración de datos (instancia completa o un solo expediente).

## 4) Cómo bajar la última versión (para cualquiera)
```bash
git fetch origin
git checkout main && git pull origin main     # o la rama que corresponda (develop/quality/Site)
```
> Todas las ramas (`main`, `develop`, `develop01`, `develop07`, `quality`, `Site`) apuntan al **mismo commit**.

## 5) Qué necesitamos de Néstor

### 5.1 Las 3 BD (para migrar + sembrar)
- **Nombres y hosts/puertos** de las 3 bases (¿dev / staging / prod? ¿un Postgres por ambiente o todos en el mismo?).
- Usuario/rol y si son alcanzables desde el contenedor `api`.
- **Semillas**: ¿sintéticas (demo) o datos reales? En producción el seed de demo se **rechaza por diseño** (sólo migramos).
- Con eso se crean `.env.dev`, `.env.staging` y `.env.prod` (copiando la sección PostgreSQL) y se corre:
  `bash scripts/migrate_seeds.sh .env.dev .env.staging .env.prod`

### 5.2 API keys / credenciales que necesita \"site\"
| Necesidad | Variables | Quién provee |
|---|---|---|
| **LLM** (chat/agentes) | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE_URL` | Cuenta del proveedor elegido (Google/Gemini, OpenAI, Anthropic, Moonshot/Kimi…) |
| **Embeddings** (búsqueda vectorial, **1024 dims**) | `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY` | Cuenta del proveedor de embeddings (puede ser la misma que la LLM) |
| **OCR Document AI** (modo `document_ai`) | `gcp-credentials.json`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_DOCUMENT_AI_PROCESSOR_ID` | GCP propio (proyecto `welladvisor`); la credencial ya existe |
| **Storage** (GCS) | `gcp-credentials.json` + `S3_BUCKET=welladvisor` + `S3_PREFIX=judicial-ai/prod` | igual que en local |
| **ASR / diarización** (opcional) | `HF_TOKEN` / `ASR_DIARIZATION_TOKEN` | Hugging Face |
| **Correo** (opcional) | `SMTP_*` | cuenta SMTP de la organización |

> ⚠️ Las API keys que se compartieron por chat (Gemini/OpenAI/Kimi) **deben rotarse**. Se copian una sola vez al `.env` del servidor; **nunca** al repo.
