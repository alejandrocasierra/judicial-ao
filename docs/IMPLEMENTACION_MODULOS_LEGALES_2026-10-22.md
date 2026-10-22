# Implementación: módulos legales

**Fecha:** 2026-10-22  
**Base:** `main` + migraciones `0055` a `0061` (la `0062` está en curso por P1)  
**Estado:** implementado en local, con pruebas y datos derivados actualizados. Cobertura legal piloto; gaps y bloqueos en [AUDITORIA_MODULOS_LEGALES_2026-10-22.md](AUDITORIA_MODULOS_LEGALES_2026-10-22.md).

---

## 1. Qué quedó implementado

### Punto 1: antialucinaciones legales

- `apps/api/app/services/legal_guard.py`: gate determinista de claims.
- Valida cita primaria, cifras, fechas, partes, normas, decisiones y contradicciones.
- Persiste `answer_audits`, `answer_audit_claims`, `answer_audit_citations` y `answer_audit_findings`.
- Integrado en `routers/query.py` antes del compositor.
- Integrado en `routers/chats.py` para asociar la auditoría al mensaje persistido.
- SSE publica `audit_status` y `audit_id` en `done`.
- API:
  - `GET /v1/cases/{case_id}/audits`
  - `GET /v1/cases/{case_id}/audits/{audit_id}`
  - `POST /v1/cases/{case_id}/messages/{message_id}/audit`
  - `POST /v1/cases/{case_id}/audits/{audit_id}/review`
  - `GET/PUT /v1/cases/{case_id}/verification-policy`

### Punto 2: segunda opinión por demanda

- `services/second_opinion.py`: corrida independiente y comparación.
- El verificador debe ser un modelo distinto; no hay fallback al mismo modelo.
- Fase 1 ciega: no recibe la respuesta original.
- Fase 2: compara claims, cifras, citas y omisiones.
- Worker `second_opinion` en la cola `default`.
- Persistencia en `second_opinion_runs`, `second_opinion_claims` y `second_opinion_findings`.
- API:
  - `POST /v1/cases/{case_id}/messages/{message_id}/second-opinion`
  - `GET /v1/cases/{case_id}/second-opinions`
  - `GET /v1/cases/{case_id}/second-opinions/{run_id}`
  - `POST /v1/cases/{case_id}/second-opinions/{run_id}/cancel`
  - `POST /v1/cases/{case_id}/second-opinions/{run_id}/review`

### Punto 3: hechos de demandante y demandado

- `services/party_facts.py`: ledger, posiciones, derivación de prueba y revisiones.
- Estados: `ALLEGED`, `ADMITTED`, `EVIDENCE_SUPPORTED`, `JUDICIALLY_PROVEN`, `DISPUTED`, `REJECTED`, `UNRESOLVED`.
- Solo `ADMITTED` y `JUDICIALLY_PROVEN` son `is_proven` y aparecen en “Probados”.
- Trigger diferido `trg_facts_proof_invariant`.
- Historial `fact_revisions` append-only.
- API:
  - `GET /v1/cases/{case_id}/facts`
  - `GET /v1/cases/{case_id}/facts/{fact_id}`
  - `GET /v1/cases/{case_id}/facts/{fact_id}/revisions`
  - `POST /v1/cases/{case_id}/facts/rebuild`
  - `PATCH /v1/cases/{case_id}/facts/{fact_id}`
  - `POST /v1/cases/{case_id}/facts/{fact_id}/review`
  - `GET /v1/cases/{case_id}/facts/export`

### UI

- Pestañas `Expediente`, `Hechos`, `Auditoría` dentro del proceso.
- `FactsBoard`: filtros Probados/Con prueba/Discutidos/Todos, lado Demandante/Demandado/Ambos, detalle, historial y revisión.
- `AuditPanel`: auditorías, claims, findings, solicitud de segunda opinión.
- `Verificación` en el sidebar: política y modelo de segunda opinión.
- Diseño responsive, teclado, estados vacíos y modo claro/oscuro.
- Build de Next.js verificado.

### Base de datos y derivados

Migraciones:

| Versión | Contenido |
|---|---|
| `0055_legal_guard` | políticas, auditorías y citas de normas |
| `0056_second_opinion` | runs, claims y findings |
| `0057_party_facts` | columnas de hechos, posiciones y revisiones; backfill |
| `0058_facts_graph_indexes` | RLS, grants, índices, trigger de prueba y chunk `fact` |
| `0059_legal_modules_purge` | permisos de purga |
| `0060_graph_fact_edges` | aristas del grafo para posiciones y decisiones |
| `0061_second_opinion_worker` | UPDATE de `second_opinion_runs` para el worker |
| `0062_legal_gate_semantics` (P1, en curso) | precios de modelo, `cost_source`, `comparison_mode` y trigger de `EVIDENCE_SUPPORTED`; no aplica en base limpia hasta corregir el splitter de `$$` |

Derivados del proceso local:

- `826` hechos con `proof_status`.
- `585` posiciones de parte.
- `1079` revisiones.
- `826` chunks `fact` con embeddings.
- `19.641` aristas de grafo, incluidas `POSITION_ON` y `DECISION_PROVES_FACT`.
- `135` normas activas por organización (`270` filas en las dos organizaciones locales), con URL oficial y checksum por fila.
- El agregado `data/legal_rules/co/checksums.json` existe y está validado;
  los checksums viajan en cada JSON y los verifica el importador.

Scripts:

- `scripts/backfill_legal_modules.py`
- `scripts/reindex_facts.py`
- `scripts/rebuild_graphs.py`
- `scripts/import_legal_rules.py`

### Contratos y harness

- `docs/CONTRATOS_HARNESS_MODULOS_LEGALES.md`: agentes/skills de construcción y runtime.
- `docs/GUIA_DISENO_MODULOS_LEGALES.md`: sidebar, tabs, responsive y accesibilidad.
- `docs/ARCHIFY_MODULOS_LEGALES.md`: plan de artefactos de arquitectura.
- `docs/architecture/`: seis diagramas Archify validados:
  - [Contexto del sistema](architecture/00-system-context.html)
  - [Gate legal](architecture/01-legal-guard-flow.html)
  - [Segunda opinión](architecture/02-second-opinion-sequence.html)
  - [Ciclo de vida de hechos](architecture/03-facts-lifecycle.html)
  - [Modelo de datos](architecture/04-data-model.html)
  - [Navegación y UX](architecture/05-ux-navigation.html)

### Prompts y schemas (cierre P2)

| Archivo | Estado | Nota |
|---|---|---|
| `packages/prompts/answer_question.v4.md` | contrato de versión | `loaded_as_prompt: false`; el runtime carga v3/v2 |
| `packages/prompts/verify_answer.v3.md` | contrato de versión | `loaded_as_prompt: false`; el runtime carga v2 |
| `packages/prompts/fact_proof_review.v1.md` | prompt nuevo | recomendación para revisión humana; sin llamada en `party_facts.py` |
| `packages/schemas/legal_audit.schema.json` | validado | Draft 2020-12; invariante PASSED sin críticos |
| `packages/schemas/second_opinion.schema.json` | validado | COMPLETED exige veredicto; FAILED exige `error_code` |
| `packages/schemas/party_fact.schema.json` | validado | `is_proven` derivado; ADMITTED y JUDICIALLY_PROVEN con su requisito |

Los schemas existentes (`fact.schema.json`, `answer.schema.json`,
`verify_answer.schema.json`) se conservan sin cambios. La validación reproducible
está en `packages/schemas/validate_schemas.py`.

---

## 2. Pruebas ejecutadas

| Suite | Resultado |
|---|---|
| Suite completa (unit, integration, static, behavior, security) | **1308 pruebas verdes** |
| Nuevas unitarias de módulos legales | 7 verdes |
| Nuevas de integración de módulos legales | 3 verdes |
| Migraciones `0055` a `0061` | upgrade/downgrade probados en BD de test |
| Build web | Next.js compila y genera las rutas nuevas |
| Salud local | API, web, worker, worker-media y MCP healthy |
| Segunda opinión real | Gemini 3.8 Flash como respondedor y DeepSeek Chat como verificador: `COMPLETED`, `PARTIAL`, 4 s, 9.095 tokens de entrada, 444 de salida, costo `0.005213` |
| Corpus | **135 normas por organización, 270 filas**, 21 fuentes, 0 filas sin checksum/URL |
| E2E | **24/24** en 360/768/1024/1440 |
| Golden set legal | **7/7** |

Comandos:

```bash
.venv/Scripts/python.exe -m pytest tests/unit -q
.venv/Scripts/python.exe -m pytest tests/integration -q
cd apps/web && npm run build
```

---

## 3. Operación

### Backfill de hechos

```bash
python scripts/backfill_legal_modules.py --all --dry-run
python scripts/backfill_legal_modules.py --all
```

### Reindexar hechos en pgvector

```bash
python scripts/reindex_facts.py --all --local-hash
```

### Reconstruir el grafo

```bash
python scripts/rebuild_graphs.py --all
```

### Cargar normas oficiales

```bash
python scripts/import_legal_rules.py --dir data/legal_rules/co --organization-id <uuid>
# o para todas las organizaciones:
python scripts/import_legal_rules.py --dir data/legal_rules/co --all
```

Sin normas cargadas, el gate bloquea las conclusiones jurídicas en lugar de
inventarlas. El corpus no se fabrica con memoria del modelo.

### Configurar segunda opinión

1. Abrir `Verificación` en el sidebar.
2. Pegar el UUID del proceso.
3. Elegir un modelo distinto del respondedor.
4. Guardar.
5. En `Auditoría`, solicitar la segunda opinión de una respuesta.

### Flags de los módulos legales

`config.py` deja los tres flags en `false` (P0-03). Se activan por entorno,
después de migrar y verificar:

```dotenv
LEGAL_GUARD_ENABLED=true     # audita toda respuesta nueva
LEGAL_GUARD_MODE=BALANCED    # STRICT | BALANCED | ADVISORY
SECOND_OPINION_ENABLED=true  # habilita POST /second-opinion
FACTS_PROOF_ENABLED=true     # declarado, todavía sin lectura en servicios
```

`.env` y `.env.test` locales ya los activan. `.env.example` documenta los tres en
`false` (un entorno nuevo nace apagado). `develop`, `quality` y producción deben
declararlos explícitamente al desplegar; si no, heredan `false`.
`PATCH /facts` y `GET /facts` no consultan `FACTS_PROOF_ENABLED`: el flag es
hoy un no-op y así se declara hasta que P1 lo conecte.

---

## 4. Decisiones operativas

- `cases` sigue siendo el proceso/demanda; no hay tabla padre nueva.
- Los procesos acumulados se modelan con `cuaderno` y documentos.
- Las tablas nuevas tienen RLS y triggers de misma organización.
- `answer_audits` y `fact_revisions` son append-only; las correcciones se registran como nuevas filas/revisiones.
- La segunda opinión no tiene tools de escritura.
- Los hallazgos críticos bloquean el claim afectado.
- El CKP exporta la versión `1.1` con auditorías, segunda opinión, posiciones y revisiones.

---

## 5. Pendiente operativo, no de diseño

El corpus oficial de normas y la clave del modelo de segunda opinión son
configuración de entorno. El sistema falla cerrado si faltan; no hay una ruta
silenciosa que invente una norma o use el mismo modelo. El comando de importación
y la política por proceso ya están implementados.


---

## 6. Estado real de cobertura

El núcleo de los tres módulos está implementado y probado. La segunda opinión real
ya se ejecutó y el corpus piloto está cargado. La cobertura legal todavía no es
completa: el corpus tiene 135 normas por organización, E2E y golden set ya están
verificados; falta la firma jurídica humana y el despliegue de producción. Los gaps concretos, con prioridad y acción, están en
[`AUDITORIA_MODULOS_LEGALES_2026-10-22.md`](AUDITORIA_MODULOS_LEGALES_2026-10-22.md).

---

## 7. Cierre P2 (2026-10-22)

Cambios de esta pasada y evidencia:

| Punto | Cambio | Evidencia |
|---|---|---|
| Archify 6/6 | `04-data-model` y `05-ux-navigation` generados y validados; índice actualizado | `validate`, `deliver`, `check` y `browser-check` en `pass` (`docs/architecture/*.finalize-summary.json`) |
| Prompts/schemas | nombres del plan creados sin duplicar prompts activos | `packages/schemas/validate_schemas.py` → `RESULT: PASS` (20 metaschemas + 11 casos); `pytest tests/static/test_schemas.py -q` → `22 passed` |
| Flags | `config.py` pasa los tres flags a `false`; local y test los activan explícitamente | unit `21 passed`; integración legal `6 passed` con la base en `0061` |
| Documentación | rango `0055`-`0061` (+`0062` P1), estado del plan, inventario y enlaces a la auditoría | este documento, plan y `docs/architecture/README.md` |

Pruebas ejecutadas en esta pasada:

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_legal_modules.py -q          # 21 passed
SKIP_DB_RESET=1 .venv/Scripts/python.exe -m pytest tests/integration/test_legal_modules_api.py -q  # 6 passed
.venv/Scripts/python.exe packages/schemas/validate_schemas.py                    # RESULT: PASS
```

Bloqueo encontrado (no es de P2): la migración `0062_legal_gate_semantics` de P1
falla en base limpia (`InvalidFunctionDefinition: no language specified`) porque
el splitter de migraciones corta el cuerpo de la función en el primer `$$`. Por
eso la integración se ejecutó con `SKIP_DB_RESET=1` sobre la base ya migrada a
`0061`, y no se declara validada la suite completa ni la migración `0062`.


## 8. Cierre de agentes (2026-10-22)

| Área | Evidencia |
|---|---|
| P0 | 135 normas/org, 270 filas, 21 fuentes, `checksums.json` reproducible, E2E 24/24, golden 7/7, migración desechable 0054→0063 |
| P1 | Gate semántico, quote_hash, contradicciones, juez `JUDGE`, comparador LLM, auto second opinion, costo/política, posiciones explícitas, invariante de evidencia, AuditBadge/diff |
| P2 | Archify 6/6, prompts/schemas, flags off por defecto, documentación |
| Validación | `1308 passed`; build web OK; Docker API/web healthy; E2E 24/24; golden 7/7 |
| Bloqueos | Firma jurídica humana; target/credenciales/backup de producción; precios reales de proveedor ausentes (costo `ESTIMATED`) |
