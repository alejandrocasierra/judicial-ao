# Plan de pruebas

## 1. Objetivo
Demostrar, de forma automática y repetible, tres cosas:

1. La plataforma **hace** lo que el SSD exige.
2. La plataforma **no hace** lo que el SSD prohíbe.
3. La plataforma resiste las amenazas del modelo T1–T15 (`SSD_ADDENDUM.md` §9).

El catálogo de cada prueba está en `TEST_CATALOG.md`, que se genera automáticamente.

## 2. Pirámide y suites

| Suite | Marker | BD | Qué cubre | Casos* |
|---|---|---|---|---|
| Unitarias | `unit` | No | Config sin defaults, i18n, RBAC, validación de archivos, contrato y grounding de respuestas, máquinas de estado | 49 |
| Estáticas | `static` | No | Secretos/URLs/correos en el código, coherencia `Settings`↔`.env.example`, catálogos i18n, SQL seguro, JSON Schemas, prompts | 58 |
| Integración | `integration` | Sí | Flujos completos de la API con PostgreSQL real | 21 |
| Seguridad | `security` | Sí | Tenants, authz, JWT, auth, inyección, cargas, mass assignment, inmutabilidad, IA hostil, errores, auditoría | 229 |
| Comportamiento | `behavior` | Sí | MUST-01…12 y NOT-01…12 (SSD §1, §13, §20, §128, §132, §133) | 26 |

\*Ejecuciones reales tras la parametrización: **383 en total**. El conteo por suite puede variar a medida que se agreguen pruebas.

```bash
bash scripts/run_tests.sh              # todo (≈10 s)
bash scripts/run_tests.sh fast         # unit + static, sin BD (<1 s) — ideal en cada guardado
bash scripts/run_tests.sh security -x  # una suite, detener al primer fallo
```

## 3. Entorno de pruebas
- `ENV_FILE=.env.test` debe tener `APP_ENV=test`. Si no, `conftest.py` aborta: así se protegen los datos reales.
- Al iniciar la sesión, la BD de pruebas se **destruye, migra y siembra**. Cada ejecución parte de un estado
  conocido. `SKIP_DB_RESET=1` omite ese paso cuando las suites no usan la BD.
- **Aislamiento entre pruebas:**
  - Las pruebas que mutan estado compartido lo restauran (`restore_user`, `try/finally` en legal hold y membresías).
  - Las pruebas que crean datos lo hacen en expedientes nuevos (`helpers.create_case`).
  - Los contadores de rate limit y el guion de `FakeLLM` se reinician antes de cada prueba.
- **IA determinista:** `LLM_PROVIDER=fake`. `FakeLLM.script` simula un modelo hostil o defectuoso: citas
  inventadas, JSON roto, fugas de datos, cifras alteradas.

## 4. Principios
1. **Sin datos quemados en las pruebas.**
   - Los IDs se resuelven vía API (fixture `ids`).
   - Credenciales, dominios y límites salen del entorno (`settings`, `email`, `password`).
   - Los marcadores de fuga vienen de las semillas (`markers`).
2. **Verificar en dos capas.** Las reglas críticas se prueban por la API **y** directamente en SQL, con el
   rol de la app (RLS) y con el rol dueño (triggers). Así se comprueba que no dependen sólo del código Python.
3. **Controles positivos.** Cada familia de pruebas negativas incluye al menos un caso que debe pasar.
   Ejemplos: `test_sec_jwt_01` y `test_sec_imm_03`. Así un 401 o un 403 no puede "pasar" por la razón equivocada.
4. **Contrato de error en cada rechazo.** `helpers.assert_error` verifica el código HTTP, el `code`, el mensaje traducido y el `request_id`, y que no haya stack trace.

## 5. Trazabilidad a requisitos

| Requisito | Pruebas |
|---|---|
| Preserva originales / checksum (§3.2, §132) | `test_must_01`, `test_it_doc_01`, `test_sec_imm_01/02`, `test_sec_upl_10` |
| Citas a página o timestamp (§132-133) | `test_must_02/03`, `test_it_cit_01/02`, `test_sec_ai_08` |
| Alegación ≠ hecho (§1, §13) | `test_must_04`, `test_not_01` |
| Contradicciones no omitidas (§133) | `test_must_05`, `test_not_03` |
| Evidencia insuficiente (§20) | `test_must_06`, `test_not_04`, `test_it_query_02` |
| Revisión humana con historial (§34, §95) | `test_must_07`, `test_not_07`, `test_it_review_01` |
| Baja confianza OCR/ASR (§134) | `test_must_08` |
| Timeline con fuentes (§132) | `test_must_09` |
| Bilingüe es/en | `test_must_10`, `test_it_i18n_01`, `test_sec_err_04`, `test_static_05/06` |
| Auditoría (§132) | `test_must_11`, `test_audit_chain.py` |
| Idempotencia (§128) | `test_must_12`, `test_not_08`, `test_it_proc_01` |
| Jurisdicción no contamina dominio (§2564) | `test_not_09` |
| Nada quemado (requisito del proyecto) | `tests/static/test_no_hardcoded.py`, `test_ut_cfg_*` |

## 6. Criterios de salida
- 100 % de las pruebas en verde en CI (`.github/workflows/ci.yml`) y en `ruff`.
- Todo defecto encontrado lleva una prueba de regresión antes de corregirse. Hallazgos de esta iteración:
  - `EmailStr` rechazaba el dominio reservado `.test`.
  - La revocación de la familia de refresh tokens se revertía por un rollback.
  - Las semillas reutilizaban un mismo hash y sal para todos los usuarios.
  - Caracteres de control codificados (`%00`) pasaban en los nombres de archivo.
  - `reviews` admitía UPDATE/TRUNCATE que no afectaban filas.
  - Una afirmación con un handle válido pero texto inventado llegaba a la respuesta. Se corrigió con grounding.

## 7. Pendiente (ver `BACKLOG.md`)
- Pruebas end-to-end del frontend (Playwright) cuando exista.
- Evaluación con el modelo real sobre `evals/golden` (métricas del §133).
- Pruebas de carga (k6) sobre `/query` y la carga de archivos.
- Pruebas de pipeline OCR/ASR con fixtures binarios reales.
