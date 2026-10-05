# SSD — Anexo de implementación v1.1

Complementa `SSD_Judicial_AI_Platform.md` (v1.0). El SSD define bien el *qué*; este anexo cierra los huecos
que impedían construir y probar: el *cómo exacto*, los límites y los criterios verificables. Donde el
anexo contradice al SSD, **prevalece el anexo**, y la diferencia se indica.

## 1. Análisis de brechas

| # | Brecha en SSD v1.0 | Resolución | Dónde |
|---|---|---|---|
| G1 | Multi-tenant descrito ("aislamiento") sin mecanismo | RLS `FORCE` por `organization_id` + `current_org()` por transacción + trigger `enforce_same_org` en cada FK + rol de app sin `BYPASSRLS` | `050_rls_grants.sql`, `030_triggers.sql` |
| G2 | Roles sin matriz de permisos | RBAC como código; permiso efectivo = rol org ∩ rol en expediente; permisos sólo-org | `config/rbac.yaml`, §4 |
| G3 | Estados epistémicos sin reglas de integridad | CHECKs y triggers en BD (no sólo API) | §5 |
| G4 | "Inmutabilidad" sin definición operativa | Triggers bloquean UPDATE de hash/URI/tamaño/nombre y DELETE; almacenamiento write-once por sha256; verificación en descarga | `protect_originals()` |
| G5 | Auditoría sin garantía de integridad | Append-only (triggers por fila y por sentencia) + cadena SHA-256 verificable | `audit_chain()` |
| G6 | Contrato de errores sin catálogo | Códigos estables + mensajes i18n + `request_id`; sin stack traces | `core/errors.py`, `packages/i18n` |
| G7 | Anti-alucinación declarativa | Sin evidencia ⇒ no se invoca el modelo; handles `E#` validados; grounding léxico y de cifras; contenido escapado | `services/answering.py` |
| G8 | Prompt injection mencionado sin control | Contenido marcado UNTRUSTED, `html.escape`, delimitadores no rompibles, pruebas con modelo hostil | `tests/security/test_prompt_injection.py` |
| G9 | Autenticación sin parámetros | argon2id, JWT alg fijado + iss/aud/typ, refresh rotativo con detección de reutilización, bloqueo, rate limit | §6 |
| G10 | Carga de archivos sin validación concreta | Lista blanca por extensión **y** firma binaria, content-type coherente, EICAR/ClamAV, límites por tipo, saneo de nombre | `services/files.py` |
| G11 | Concurrencia en revisión no definida | Bloqueo optimista (`expected_version`) y 409 `VERSION_CONFLICT` | `routers/review.py` |
| G12 | Costos sin límite | Presupuesto de tokens por expediente (409 `BUDGET_EXCEEDED` antes de llamar al modelo) | `CASE_MAX_*` |
| G13 | Idioma no especificado | Bilingüe es/en extremo a extremo; FTS `simple` para no sesgar a un idioma | §7 |
| G14 | Sin estrategia de configuración | 100 % variables de entorno, `Settings` sin defaults, guardas de producción | `.env.example` |
| G15 | Sin datos ni cuentas de prueba | Semillas sintéticas en 2 organizaciones con casos límite deliberados | `seed_data.yaml` |
| G16 | Criterios de aceptación (§132-133) no verificables | Convertidos en pruebas `test_must_*` / `test_not_*` | `tests/behavior` |

## 2. Decisiones de arquitectura (ADR resumidos)

| ADR | Decisión | Alternativa descartada | Motivo |
|---|---|---|---|
| 1 | Aislamiento en la BD (RLS), además de en la API | Solo filtros en la API | Un bug de la API no debe filtrar datos entre tenants |
| 2 | Responder 404 (no 403) a quien no es miembro | Responder 403 | No revelar la existencia de un expediente |
| 3 | SQL explícito con parámetros enlazados + migración SQL | ORM completo | Control total de RLS y triggers; auditable |
| 4 | FTS `simple` en el MVP; embeddings en el Sprint 3 | Stemming solo para español | Bilingüe sin sesgo; pgvector ya provisionado |
| 5 | Grounding léxico y de cifras como red mínima | Confiar en el modelo | Las cifras y fechas inventadas son el error más dañino en lo jurídico |
| 6 | Prompts versionados e inmutables (`*.vN.md`) y registrados en `model_runs` | Prompts en el código | Reproducibilidad y trazabilidad |
| 7 | `FakeLLM` determinista en dev/test; prohibido en producción | Mocks ad hoc | Pruebas reproducibles de salidas hostiles |
| 8 | Rol de la app sin DELETE sobre la evidencia | DELETE con validación en la API | Defensa en profundidad |

## 3. Modelo de datos (resumen)

**Organización y usuarios**
- `organizations`, `users` (email en minúsculas, `locale` es/en, bloqueo).
- `refresh_tokens`: sólo se guarda el hash.
- `case_members`: roles OWNER, LAWYER, REVIEWER y VIEWER.

**Expediente**: `cases`. Guarda el estado, `legal_hold`, `retention_status`, `version`, el presupuesto y lo gastado.

**Fuentes**
- `documents`: inmutable, `sha256` único por caso, estado de procesamiento. Sus páginas van en `document_pages` (texto, `ocr_confidence`, folio, tsvector).
- `media`: inmutable. Sus hablantes van en `speakers` y la transcripción en `transcript_segments` (inicio y fin en ms, confianza, tsvector).

**Conocimiento**
- `parties`, `entities`, `events` (con precisión de fecha y confianza del timeline).
- `claims`: tipo, parte que afirma y salida original de la IA.
- `facts`: estado epistémico y decisión. Se vinculan con `fact_claims` (asserts o disputes).
- `evidence` y `evidence_links` (supports, contradicts o neutral).
- `decisions`, `contradictions` (revisión humana obligatoria), `legal_rules` e `issues`.

**Trazabilidad**
- `citations`: documento+página+caracteres+`quote_hash`, o media+segmento+ms.
- `jobs`: clave de idempotencia.
- `model_runs`: prompt con versión, hash de entrada y tokens.
- `reviews` y `audit_logs`: sólo admiten inserciones.
- `chunks`: `vector(EMBEDDING_DIMENSIONS)`.

## 4. Matriz RBAC efectiva

| Permiso | ORG_ADMIN | CASE_MANAGER | LAWYER | REVIEWER | ANALYST | READ_ONLY |
|---|---|---|---|---|---|---|
| case.create | ✔ | ✔ | ✔ | | | |
| case.read / document.read / media.read | ✔ | ✔* | ✔* | ✔* | ✔* | ✔* |
| case.write / job.run / upload | ✔ | ✔* | ✔* | | | |
| case.members.manage | ✔ | ✔* (OWNER) | | | | |
| document.download | ✔ | ✔* | ✔* | ✔* | | |
| ai.query | ✔ | ✔* | ✔* | ✔* | ✔* | |
| review.write | ✔ | ✔* | ✔* | ✔* | | |
| legal_hold.manage · evidence.deletion_request · audit.read · user.manage | ✔ | | | | | |

\* Además requiere membresía del expediente, con un rol de caso que también conceda el permiso.

Quien no es miembro recibe **404**. Un miembro sin el permiso recibe **403**. La matriz completa se prueba en
`tests/security/test_authz_matrix.py`.

## 5. Invariantes epistémicas (impuestas en BD)

| Regla | Mecanismo |
|---|---|
| `JUDICIALLY_DETERMINED` ⇒ decisión citada del mismo caso | CHECK + trigger `fact_decision_same_case` |
| Hablante `CONFIRMED` ⇒ parte resuelta | CHECK en `speakers` |
| Contradicción ⇒ `human_review_required = true` | CHECK (constante) |
| Cita ⇒ (documento + página) XOR (media + segmento + ms válidos) | CHECK en `citations` |
| Citas de respuesta sólo a evidencia recuperada | Validación de handles en `parse_and_validate` |

## 6. Seguridad de autenticación (parámetros por entorno)

- **Contraseñas:** argon2id con política de longitud `PASSWORD_MIN_LENGTH`.
- **Bloqueo:** tras `AUTH_MAX_FAILED_ATTEMPTS` intentos fallidos, durante `AUTH_LOCKOUT_SECONDS`.
- **Access token JWT:** vida `ACCESS_TOKEN_TTL_SECONDS`. El algoritmo está fijado y se exigen `iss`, `aud`, `typ`, `exp` y `sub`.
- **Autorización:** el rol que viene en el token se ignora; se usa el rol vigente en la BD.
- **Refresh token:** vida `REFRESH_TOKEN_TTL_SECONDS` y rota en cada uso. Reutilizar uno revocado revoca toda la familia del usuario, y esa revocación se confirma antes de responder 401.
- **Anti-enumeración:** usuario inexistente, contraseña errónea y cuenta inactiva reciben la misma respuesta. La verificación toma el mismo tiempo aunque el usuario no exista.

## 7. Contrato de API y errores
Todas las rutas van bajo `API_PREFIX`, y OpenAPI se genera automáticamente. Los errores tienen esta forma:
`{"error":{"code","message","request_id","details?"}}`.

- `code` es estable y `message` se traduce según `Accept-Language` o el `locale` del usuario.
- Nunca se incluyen stack traces ni se repite la entrada.
- Las rutas inexistentes y los métodos no permitidos (404/405) usan el mismo contrato.

Cabeceras de seguridad en todas las respuestas:

| Cabecera | Valor |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `no-referrer` |
| `Cache-Control` | `no-store` |
| `Content-Security-Policy` | `default-src 'none'` |
| `Strict-Transport-Security` | sólo en staging y producción |

`X-Request-ID` se sanea y se propaga a la auditoría.

## 8. Máquinas de estado
Expediente: `CREATED → UPLOADING/INGESTING → PROCESSING → PARTIALLY_READY/READY_FOR_REVIEW → REVIEWING → READY → ARCHIVED`
(`FAILED` permite reintentar). Job: `QUEUED → RUNNING → SUCCEEDED | FAILED ↔ RETRYING | CANCELLED`.
Los errores transitorios se reintentan; los deterministas pasan a revisión manual (`domain/states.py`).

## 9. Modelo de amenazas → controles → pruebas

| ID | Amenaza | Control | Prueba |
|---|---|---|---|
| T1 | Acceso a datos de otro tenant | RLS FORCE, `enforce_same_org`, 404 | `test_tenant_isolation.py` (SEC-TEN-01…16) |
| T2 | Escalamiento de privilegios | RBAC org∩caso, rol leído de BD | `test_authz_matrix.py`, `test_sec_jwt_09` |
| T3 | Suplantación / fuerza bruta | JWT estricto, refresh rotativo, bloqueo, rate limit | `test_jwt.py`, `test_auth_hardening.py` |
| T4 | Robo de refresh token | Detección de reutilización ⇒ revocación de familia | `test_it_auth_02` |
| T5 | Inyección SQL | Sólo parámetros; f-strings con identificadores en lista blanca | `test_injection.py`, `test_static_07` |
| T6 | Archivo malicioso | Magic bytes, lista blanca, antivirus, límites, saneo | `test_uploads.py` |
| T7 | Manipulación de evidencia | Triggers de inmutabilidad, sha256, verificación en descarga, legal hold | `test_immutability.py`, `test_sec_upl_10` |
| T8 | Borrado o alteración de auditoría | Append-only + cadena de hash | `test_audit_chain.py`, `test_sec_imm_05/06` |
| T9 | Prompt injection vía documentos | Escape, UNTRUSTED, delimitadores | `test_sec_ai_01…03`, `test_not_11` |
| T10 | Alucinación / cita falsa | Handles validados, grounding de términos y cifras | `test_sec_ai_04…07`, `test_not_05` |
| T11 | Fuga entre tenants vía el modelo | Recuperación bajo RLS + grounding | `test_sec_ai_07`, `test_sec_ten_06` |
| T12 | Asignación masiva | DTO `extra=forbid`, lista blanca de revisión | `test_mass_assignment.py` |
| T13 | Filtración por errores | Handler genérico, sin eco de entrada | `test_errors.py`, `test_sec_inj_08` |
| T14 | Secretos en el repositorio | `.gitignore`, `gen_env.py`, análisis estático | `test_no_hardcoded.py` |
| T15 | Costos desbocados | Presupuesto por caso antes de invocar el modelo | `test_not_10` |

## 10. Fuera de alcance del MVP (explícito)
- Firma digital de exportaciones.
- Cifrado por campo con KMS. El MVP depende del cifrado del volumen y de S3 SSE.
- SSO/SAML.
- Retención automática por jurisdicción. Hoy sólo existe `legal_hold` manual.
