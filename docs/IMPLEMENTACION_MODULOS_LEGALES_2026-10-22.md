# Implementación: módulos legales

**Fecha:** 2026-10-22  
**Base:** `main` + migraciones `0055` a `0061`  
**Estado:** implementado en local, con pruebas y datos derivados actualizados.

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

Derivados del proceso local:

- `826` hechos con `proof_status`.
- `585` posiciones de parte.
- `1079` revisiones.
- `826` chunks `fact` con embeddings.
- `19.641` aristas de grafo, incluidas `POSITION_ON` y `DECISION_PROVES_FACT`.
- `38` normas activas por organización (`76` filas en las dos organizaciones locales), con URL oficial y checksum.

Scripts:

- `scripts/backfill_legal_modules.py`
- `scripts/reindex_facts.py`
- `scripts/rebuild_graphs.py`
- `scripts/import_legal_rules.py`

### Contratos y harness

- `docs/CONTRATOS_HARNESS_MODULOS_LEGALES.md`: agentes/skills de construcción y runtime.
- `docs/GUIA_DISENO_MODULOS_LEGALES.md`: sidebar, tabs, responsive y accesibilidad.
- `docs/ARCHIFY_MODULOS_LEGALES.md`: plan de artefactos de arquitectura.
- `docs/architecture/`: cuatro diagramas Archify validados:
  - [Contexto del sistema](architecture/00-system-context.html)
  - [Gate legal](architecture/01-legal-guard-flow.html)
  - [Segunda opinión](architecture/02-second-opinion-sequence.html)
  - [Ciclo de vida de hechos](architecture/03-facts-lifecycle.html)

---

## 2. Pruebas ejecutadas

| Suite | Resultado |
|---|---|
| `tests/unit` | 693 pruebas verdes |
| `tests/integration` | 152 pruebas verdes |
| Nuevas unitarias de módulos legales | 7 verdes |
| Nuevas de integración de módulos legales | 3 verdes |
| Migraciones `0055` a `0061` | upgrade/downgrade probados en BD de test |
| Build web | Next.js compila y genera las rutas nuevas |
| Salud local | API, web, worker, worker-media y MCP healthy |
| Segunda opinión real | Gemini 3.8 Flash como respondedor y DeepSeek Chat como verificador: `COMPLETED`, `PARTIAL`, 4 s, 9.095 tokens de entrada, 444 de salida, costo `0.005213` |
| Corpus piloto | 38 normas por organización, 76 filas en total; CGP/Ley 1564, Código Civil/Ley 57 y Código de Comercio/Decreto 410 |

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
completa: el corpus tiene 38 normas por organización y faltan E2E, golden set y
despliegue de producción. Los gaps concretos, con prioridad y acción, están en
[`AUDITORIA_MODULOS_LEGALES_2026-10-22.md`](AUDITORIA_MODULOS_LEGALES_2026-10-22.md).
