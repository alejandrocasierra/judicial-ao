# Operación de despliegue — módulos legales

**Fecha:** 2026-10-22  
**Estado:** runbook listo y probado en seco; **producción no desplegada**.  
**Target identificable en el repo:** instancias `site` / `develop` / `quality`
(`.env.advisorlegal`, `.env.develop`, `.env.quality`), VPS `/var/www/advisorlegal.co`,
Postgres compartido `judicial-infra-postgres-1`, bucket GCS `welladvisor`.

> En esta sesión no hay acceso al VPS, ni a las credenciales de producción, ni
> autorización explícita. No se ejecutó `deploy_all.sh`, `deploy.sh` ni ninguna
> migración contra producción. Este documento no afirma lo contrario.

---

## 1. Qué hay en el repo y qué falta

| Pieza | Estado |
|---|---|
| `docker-compose.prod.yml` | Overlay de Caddy/TLS; no define target por sí solo. |
| `.env.production.example` | Plantilla; dominios `app.tudominio.com`, `STORAGE_BACKEND=gcs`, bucket `welladvisor`. |
| `.gitlab-ci.yml` | Solo construye y publica imágenes; **no despliega**. |
| `scripts/deploy_all.sh` | Despliegue real de las 3 instancias en el VPS. |
| `scripts/backup_gcs.sh` | Backup diario a `gs://welladvisor/judicial-ai/backups`. |
| `scripts/deploy_legal_modules.ps1` | Runbook ejecutable P0 (dry-run por defecto). |
| Credenciales / acceso VPS | **No disponibles en esta sesión.** |
| Autorización de producción | **No otorgada.** |
| Backup verificado de producción | **No existe en esta sesión.** |

**Conclusión:** el despliegue de producción queda **bloqueado** hasta que el
responsable aporte target, credenciales, autorización y un backup verificado.
El script está listo para ejecutarse en esa ventana.

---

## 2. Precondiciones de la ventana

1. Autorización escrita del responsable de producción.
2. Target confirmado: instancia(s) y base(s). No se acepta “producción” genérica.
3. Backup reciente verificado con `pg_restore -l` (el script lo hace y aborta si falla).
4. Ventana sin despliegues automáticos (`auto_deploy.sh` corre cada 10 min).
5. Variables de entorno del target presentes (`ENV_FILE`).
6. Corpus `data/legal_rules/co/` con `checksums.json` validado:
   ```bash
   python scripts/build_checksums.py --check
   ```
7. Revisión humana del acta: `docs/ACTA_REVISION_CORPUS_LEGALES.md`. **Sin firma,
   el despliegue es técnico, no una aceptación legal.**

---

## 3. Secuencia recomendada

### 3.1 Ensayo en seco (obligatorio)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy_legal_modules.ps1 `
  -Target local-test `
  -CaseId 38865959-8d5f-44e4-bbc8-1226bd345b00
```

Salida esperada: backup simulado, `alembic upgrade 0063_legal_gate_semantics`,
backfill, reindex, grafo, smoke y `=== Despliegue terminado (local-test) ===`.
No toca la base.

### 3.2 Producción (solo con autorización)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy_legal_modules.ps1 `
  -Target production `
  -EnvFile .env.advisorlegal `
  -ProdOverlay `
  -Execute `
  -AuthorizeProduction `
  -ConfirmProduction "DEPLOY-PRODUCTION" `
  -ToHead `
  -ImportCorpus `
  -CaseId <uuid-del-proceso-piloto>
```

Notas:

- `-ToHead` usa el head actual de Alembic. En la auditoría P0 el head era
  `0061_second_opinion_worker`; el repo actual incluye `0062` y `0063` de P1.
  Si se quiere congelar el alcance P0, usar
  `-TargetRevision 0063_legal_gate_semantics`.
- `-ImportCorpus` carga las 135 normas en todas las organizaciones. Si el corpus
  ya está cargado, es idempotente por `(organization_id, jurisdiction, source,
  identifier, version_date)`.
- `-CaseId` limita backfill/reindex/grafo al proceso piloto. Para todos, omitirlo
  (usa `--all`).
- **No usar `-ReindexLocalHash` en producción**: es un modo de prueba.

### 3.3 Qué hace cada paso

| Paso | Comando interno | Efecto |
|---|---|---|
| Backup | `pg_dump -Fc` + `pg_restore -l` | Dump verificado en `var/backups/legal-modules/`. |
| Migración | `alembic upgrade <target>` | Crea tablas/columnas/triggers de los módulos legales. |
| Corpus | `import_legal_rules.py --all` | Inserta/actualiza normas con checksum. |
| Backfill | `backfill_legal_modules.py --all` | Reconstruye el ledger de hechos por parte. |
| Reindex | `reindex_facts.py --all` | Recalcula embeddings de chunks `fact`. |
| Grafo | `rebuild_graphs.py --all` | Reconstruye nodos/aristas del proceso. |
| Smoke | `/health` + conteos SQL | Verifica API, normas activas, auditorías y hechos. |

---

## 4. Verificación posterior

```sql
-- Normas activas por organización
SELECT organization_id, count(*) FROM legal_rules WHERE is_active GROUP BY 1;
-- Debe haber 97 por organización (o el número del corpus desplegado).

-- Integridad del corpus
SELECT count(*) FROM legal_rules
WHERE is_active AND (checksum IS NULL OR official_url IS NULL);
-- Debe ser 0.

-- Auditorías y hechos
SELECT count(*) FROM answer_audits;
SELECT count(*) FROM facts WHERE proof_status IS NOT NULL;

-- Citas de normas persistidas
SELECT count(*) FROM legal_rule_citations;
```

Smoke HTTP:

```bash
curl -fsS https://<dominio>/health
```

Además, ejecutar el golden set contra la base del target (lectura):

```bash
python evals/golden/legal_modules_runner.py
```

---

## 5. Rollback

### 5.1 Esquema

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy_legal_modules.ps1 `
  -Target production -EnvFile .env.advisorlegal -ProdOverlay -Execute `
  -AuthorizeProduction -ConfirmProduction "DEPLOY-PRODUCTION" `
  -RollbackTo 0054_chat_session_touch
```

Esto solo baja Alembic. **No** borra datos de negocio automáticamente. Revisar
qué migraciones son reversibles (`0055`–`0061` tienen `downgrade`).

### 5.2 Restauración completa

Solo con autorización expresa y ventana de mantenimiento:

```powershell
# El dump está en var/backups/legal-modules/judicial-<timestamp>.dump
docker compose --env-file .env.advisorlegal -f docker-compose.yml -f docker-compose.prod.yml `
  exec -T postgres pg_restore -U postgres -d judicial --clean --if-exists /tmp/restore.dump
```

`-RestoreBackup` existe en el script, pero está desactivado por defecto y no se
probó contra producción. Es destructivo.

### 5.3 Criterio de aborto

Abortar y restaurar si:

- el backup no lista con `pg_restore -l`;
- la migración falla o deja `alembic current` distinto del esperado;
- el smoke no devuelve `/health` 200;
- `legal_rules` activas queda en 0;
- el backfill/reindex/grafo falla de forma no idempotente.

---

## 6. Evidencia de las pruebas realizadas en esta sesión

### 6.1 Dry-run del script

```text
Target=local-test Execute=False EnvFile=.env Compose=docker-compose.yml
Backup pg_dump -Fc (judicial) -> ... (simulado)
Alembic upgrade 0063_legal_gate_semantics -> ... (simulado)
Backfill del ledger de hechos -> ... --case-id 38865959-...
Reindexar chunks estructurados -> ...
Reconstruir grafo -> ...
Smoke: health http://127.0.0.1:8000/health
=== Despliegue terminado (local-test) ===
```

### 6.2 Migración en base desechable

Se creó `judicial_p0_migration_test` (PostgreSQL local, puerto 54322), se
ejecutó y se eliminó:

```text
alembic upgrade 0054_chat_session_touch   -> OK
alembic upgrade 0061_second_opinion_worker -> OK   (current = 0061)
alembic downgrade 0054_chat_session_touch -> OK   (current = 0054)
alembic upgrade head                      -> OK   (current = 0063_legal_gate_semantics)
DROP DATABASE judicial_p0_migration_test  -> OK
```

Esto prueba el tramo `0054 -> 0061`, el downgrade y el upgrade al head actual.
**No** prueba producción: no hubo target, credenciales ni backup de producción.

---

## 7. Riesgos y bloqueos

| Riesgo | Mitigación |
|---|---|
| Migración `0062`/`0063` de P1 incluidas por `head` | Usar `-TargetRevision 0063_legal_gate_semantics` si el alcance es solo P0. |
| Corpus incompleto (faltan leyes citadas) | El gate falla cerrado; ampliar corpus con revisión legal. |
| Sin firma jurídica | No declarar “corpus oficial definitivo”; ver acta. |
| Backfill/reindex/grafo costosos | Ventana y `-CaseId` para el piloto; monitorear worker. |
| `auto_deploy.sh` concurrente | Detener cron o ejecutar fuera de la ventana de 10 min. |
| Rollback de datos | Backup verificado + restauración explícita; no automática. |

**Bloqueo actual:** falta target/credenciales/autorización y backup de
producción. Sin ellos no se ejecuta nada contra producción.
