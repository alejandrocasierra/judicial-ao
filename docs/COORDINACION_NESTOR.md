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
| **Migraciones + semillas de las 3 BD** | ✅ script listo | `bash scripts/migrate_seeds.sh .env.dev .env.staging .env.prod` (siembra sólo en dev; en prod se omite) |
| **API keys de "site"** | ⏳ falta info | Van en el `.env` del servidor (nunca al repo). Se necesitan: `LLM_API_KEY`, `EMBEDDING_API_KEY`, y (si aplica) Google Document AI y storage `S3_*`/GCS |
| **Node.js para el frontend** | ✅ no hace falta | El frontend se despliega como **imagen Docker** (build con `node:22` dentro). Sólo se necesita Node en el host si se compila fuera de Docker |
| **Límite de 100 MB de Cloudflare (videos grandes)** | ✅ implementado | **Subida directa a GCS/S3** por URL prefirmada (`/uploads/presign` + `/uploads/complete`), sin pasar por el proxy de Cloudflare. Alternativa/simple: subdominio **solo-DNS (gris)** para subidas |

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
1. Nombres/hosts de **las 3 BD** (¿dev/staging/prod?) y si las semillas son **sintéticas** o datos reales.
2. Qué **API keys** necesita "site" y quién las provee.
3. Confirmar el **storage** de producción (GCS o S3) para la subida directa (bucket, región, prefijo).
