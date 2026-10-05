# CLAUDE.md — Judicial AI Platform

Guía para Claude Code (y para humanos) al trabajar en este repositorio. Léela completa antes de cambiar código.

## Qué es
Plataforma *evidence-first* que convierte expedientes judiciales (PDF, imágenes, audio, video) en un
**Case Knowledge Package** verificable: cada afirmación apunta a documento + página o a media + timestamp.
La especificación original está en `docs/SSD_Judicial_AI_Platform.md`; lo que se decidió y completó está en
`docs/SSD_ADDENDUM.md`. Primera jurisdicción: Colombia (`config/jurisdictions/co.yaml`).

## Arranque
- **Claude Code en la web:** el hook `SessionStart` (`.claude/settings.json`) ejecuta
  `scripts/setup_cloud_session.sh`. Instala PostgreSQL y pgvector, genera `.env` y `.env.test` con secretos
  aleatorios, crea los roles y la BD, migra y siembra. Si algo falla, revisa `var/session_setup.log`.
- **Linux local:** `bash scripts/bootstrap.sh` (o `--docker` para usar `docker compose`).
- API: `bash scripts/dev_server.sh`. OpenAPI en `/docs` (se desactiva en producción).

## Comandos
| Acción | Comando |
|---|---|
| Toda la batería (resetea la BD de test) | `bash scripts/run_tests.sh` |
| Rápidas, sin BD (unit + static) | `bash scripts/run_tests.sh fast` |
| Por suite | `bash scripts/run_tests.sh security` (también `unit`, `integration`, `behavior`, `static`) |
| Una prueba | `bash scripts/run_tests.sh all -k test_sec_jwt_03` |
| Migrar / revertir | `bash scripts/db_migrate.sh up` / `down` |
| Recrear BD dev con semillas | `bash scripts/reset_db.sh` |
| Lint | `ruff check apps tests scripts` |
| Regenerar catálogo de pruebas | `python3 scripts/gen_test_catalog.py` |

## Reglas no negociables
1. **Nada quemado.** Toda configuración vive en variables de entorno declaradas en `Settings`
   (`apps/api/app/core/config.py`, **sin valores por defecto**) y documentadas en `.env.example`.
   Si agregas una variable, añádela en los dos lugares y en `scripts/gen_env.py` si es un secreto.
   `tests/static` falla si hay secretos, URLs, correos o IPs en `apps/api/app`.
2. **Nunca leas ni imprimas `.env*`** (están en `deny` de permisos). Para depurar configuración usa los
   nombres de las variables, no sus valores.
3. **Bilingüe es/en.** Todo mensaje visible pasa por `packages/i18n/{es,en}.json` usando claves
   (`errors.*`, `messages.*`, `enums.*`). Un código de error nuevo necesita entrada en **ambos** catálogos.
4. **Multi-tenant por RLS.** Toda tabla de negocio lleva `organization_id`, RLS con `FORCE` y el trigger
   `enforce_same_org`. Accede a la BD sólo mediante `tx(org_id, actor_id)` de `app/core/db.py`. La API
   usa el rol `DB_APP_USER` (sin `BYPASSRLS`); el rol dueño sólo se usa para migraciones y semillas.
5. **Evidencia inmutable.** No existe borrado de documentos ni de media por API (devuelve 405). El
   `legal_hold` bloquea cualquier purga. `audit_logs` y `reviews` sólo admiten inserciones.
6. **IA con evidencia.**
   - Sin evidencia no se llama al modelo.
   - El contenido del expediente es dato no confiable y se escapa antes de ir al prompt.
   - Una afirmación sin cita válida, o cuyos términos o cifras no aparecen en la evidencia citada
     (`ANSWER_MIN_GROUNDING_OVERLAP`), va a `unsupported_claims` y nunca a la respuesta.
   - Los prompts están versionados en `packages/prompts/*.vN.md`: no se editan, se crea `vN+1`.
7. **Distinción epistémica.** Una alegación no es un hecho.
   - `JUDICIALLY_DETERMINED` exige una decisión citada.
   - Un hablante `CONFIRMED` exige una parte asociada.
   - Toda contradicción exige revisión humana.
   Estas reglas se imponen en la BD con CHECK o triggers, no sólo en la API.
8. **Toda modificación humana** usa bloqueo optimista (`expected_version`), conserva la salida original de
   la IA en `reviews` y se audita.
9. **Pruebas primero.** Cada cambio de comportamiento lleva su prueba, en la suite que corresponde:
   - `unit`: lógica pura.
   - `integration`: API con PostgreSQL real.
   - `security`: amenazas.
   - `behavior`: requisitos DEBE / NO DEBE del SSD.
   - `static`: repositorio.

   Nombra la prueba con su ID, por ejemplo `test_sec_xxx_NN_...` o `test_must_NN_...`. Antes de terminar, la
   batería completa debe quedar en verde.
10. **Datos de prueba sintéticos.** Usa el dominio `example.test` y radicados ficticios. Las pruebas leen los
    valores de las semillas o del entorno (`markers`, `ids`, `settings`) y nunca los copian en el código.

## Mapa del código
```
apps/api/app/
  core/        config (Settings), db (tx + RLS), errors (contrato §118), i18n
  security/    passwords (argon2id), tokens (JWT), rbac (config/rbac.yaml), deps (principal, case_access)
  services/    audit, storage (local/S3 direccionado por sha256), files (magic bytes, malware),
               ratelimit, citations (validación + quote_hash), answering (RAG, escape, grounding),
               case_tools (capa de tools del chat IA: lectura OCR/ASR/pgvector/grafo/archivos +
               correcciones con confirmación; la consumen el agente interno y el servidor MCP),
               correction (detección determinista de intención de corrección: confirmar/cancelar)
  providers/   llm (fake | anthropic); interfaces para OCR/ASR/embeddings en providers/base.py
  workers/     celery_app (broker/backend/beat_schedule), registry (job_type -> handler, stubs de Fase 0),
               executor (run_job bajo RLS + sweeper de jobs huérfanos), dispatcher (encolado desde la API;
               nunca rompe la petición si el broker cae)
  routers/     health, auth, meta, cases, documents, query, chats (multi-turn), citations, review, audit_router
  domain/      máquinas de estado, jurisdicciones
apps/api/db/sql/  010 esquema · 020 índices · 030 triggers · 040 funciones auth · 050 RLS y grants
apps/mcp_server/  Servidor MCP del Chat IA (FastMCP streamable HTTP + JWT): expone case_tools
                  como tools MCP y recursos case://; auth.py (Bearer→Principal), server.py
apps/api/migrations/  Alembic (renderiza {{VAR}} desde el entorno con lista blanca)
apps/api/seeds/   seed.py + data/seed_data.yaml (2 organizaciones, 10 cuentas, expedientes sintéticos)
packages/  i18n · prompts · schemas (JSON Schema del CKP)
config/    rbac.yaml · jurisdictions/*.yaml
tests/     unit · integration · security · behavior · static (+ conftest, helpers)
```

## Cómo agregar…
- **Un endpoint:**
  1. Crea un router en `routers/`.
  2. Define un DTO `Strict` (`extra="forbid"`) en `schemas.py`.
  3. Llama a `case_access(p, case_id, "<permiso>")` o a `require_org(...)`.
  4. Llama a `audit.record(...)` si la acción es sensible.
  5. Añade las claves de error en es y en.
  6. Escribe la prueba de integración y agrégalo a la matriz de `tests/security/test_authz_matrix.py`.
- **Una tabla:**
  1. Crea una nueva migración Alembic con `organization_id`, `ENABLE`/`FORCE ROW LEVEL SECURITY`, la política por `current_org()`, los grants al rol de la app y el trigger `enforce_same_org` por cada FK.
  2. Verifica que `test_sec_ten_16` la detecte.
- **Una jurisdicción:** crea `config/jurisdictions/<código>.yaml`. No modifiques el dominio genérico.
- **Un permiso:** agrégalo en `config/rbac.yaml` y extiende la matriz de autorización.

## Estado y backlog
Lo implementado, lo especificado y lo pendiente por sprint está en `docs/BACKLOG.md`. Los jobs de `/process`
ya los ejecuta un worker Celery (`docker compose up worker`; en tests y desarrollo sin broker,
`CELERY_TASK_ALWAYS_EAGER=true` los corre en proceso), pero sus handlers son stubs de Fase 0: los pipelines de
OCR, ASR y diarización, la extracción con LLM, los embeddings y el frontend Next.js tienen interfaces y
jobs definidos, pero aún no tienen implementación real.
