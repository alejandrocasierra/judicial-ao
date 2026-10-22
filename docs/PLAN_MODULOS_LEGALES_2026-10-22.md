# Plan de implementación: anti-alucinación legal, segunda opinión y hechos probados

**Versión:** 1.1 (estado al 2026-10-22, cierre P2)  
**Fecha:** 2026-10-22  
**Base verificada:** rama `main`, commit `963297c`  
**Estado de ejecución:** implementado y probado en local sobre las migraciones `0055` a `0061`; la `0062` está en curso por P1. La cobertura legal todavía es piloto. Estado real, pruebas y gaps: [AUDITORIA_MODULOS_LEGALES_2026-10-22.md](AUDITORIA_MODULOS_LEGALES_2026-10-22.md). Diagramas: [architecture/README.md](architecture/README.md).  
**Alcance:** tres módulos nuevos sobre el producto actual, sin reescribir el pipeline ni romper los cuatro casos cerrados.  
**Documentos hermanos:**
- [Implementación y evidencia](IMPLEMENTACION_MODULOS_LEGALES_2026-10-22.md)
- [Contratos de harness](CONTRATOS_HARNESS_MODULOS_LEGALES.md)
- [Guía de diseño web y responsive](GUIA_DISENO_MODULOS_LEGALES.md)
- [Plan de diagramas Archify](ARCHIFY_MODULOS_LEGALES.md)
- [Arquitectura generada](architecture/README.md)
- [Auditoría de módulos legales](AUDITORIA_MODULOS_LEGALES_2026-10-22.md)

---

## 0. Resumen ejecutivo

El proyecto ya tiene la materia prima correcta: `cases` representa un proceso, las carpetas y documentos están scoped por `case_id`, hay OCR/ASR, chunks con pgvector, un grafo de conocimiento, `claims`, `facts`, `evidence`, `decisions`, `citations`, `reviews`, RLS multitenant y un verificador semántico. Lo que falta no es “otro chatbot”: falta convertir esas piezas en **tres contratos de publicación** verificables.

| Punto | Resultado que se entrega | Decisión central |
|---|---|---|
| **1. Antialucinaciones legales** | Un `legal_guard` obligatorio para toda respuesta publicada: afirma solo lo que una fuente primaria sostiene, con citas, cifras, fechas, nombres, partes, normas y decisiones verificadas; lo demás se elimina o se marca para revisión. | La respuesta no se publica como hecho si no pasa el gate. El juez semántico complementa, no reemplaza, la validación determinista. |
| **2. Segunda opinión por demanda** | Auditoría independiente de una respuesta con **otro modelo** y comparación de claims, citas y hallazgos, por proceso/demanda y bajo demanda. | Fase 1 independiente (el segundo modelo no ve la respuesta del primero); fase 2 comparación. Asíncrona, idempotente y con costo acotado. |
| **3. Hechos de demandante y demandado** | Un ledger de hechos con posición de cada parte y estado de prueba; por defecto solo muestra `ADMITTED` y `JUDICIALLY_PROVEN`. | Un hecho no es “probado” por opinión del modelo: exige admisión de parte o decisión judicial citada. `EVIDENCE_SUPPORTED` se muestra aparte, nunca como probado. |

**Proceso = demanda.** Se mantiene `cases` como unidad canónica. Un expediente acumulado sigue siendo un `case` con sus `cuaderno`, `indice_numero` y documentos. No se crea una tabla padre nueva ni un singleton global. Todo lo nuevo lleva `organization_id` y `case_id`, RLS y trigger de misma organización, para que N procesos funcionen desde el primer día.

**No hay decisiones abiertas.** Las decisiones de arquitectura, datos, permisos, modelos, UX y despliegue están en la sección 2. Los casos sin modelo o sin norma oficial se resuelven con un bloqueo explícito y una acción de configuración, nunca con una respuesta inventada.

---

## 1. Línea base verificada en el código

### 1.1 Lo que ya existe y se reutiliza

| Capacidad | Implementación actual | Uso en este plan |
|---|---|---|
| Proceso/demanda | `cases` con `case_number`, `jurisdiction`, `court`, `chamber`, `title`, `status`, `legal_hold`; `case_members`; RLS | Unidad de todos los módulos. N procesos por organización. |
| Carpetas y archivos | `case_folders` jerárquico, `documents.folder_id`, `case_files.folder_id`, `GET /cases/{id}/files` | La navegación y el alcance por proceso se conservan. |
| Texto primario | `document_pages.text`, `transcript_segments.text`, `document_ocr_versions` | Fuente de verdad para citas y para probar hechos. |
| Búsqueda | `chunks` con `embedding vector`, HNSW, `search_tsv` GIN, `answering.retrieve`, `retrieval_plan` | Recuperación de evidencia; se añade filtro por tipo/estado de hecho. |
| Grafo | `graph_nodes`, `graph_edges`, `services/graph.py` | Se añaden nodos/aristas de hechos y posiciones de parte. |
| Conocimiento | `claims`, `facts`, `fact_claims`, `evidence`, `evidence_links`, `decisions`, `contradictions`, `legal_rules` | Base del ledger de hechos y de la verificación de normas. |
| Citas | `citations` con `target_type`, página/segmento, `quote_hash`; `GET /citations/{id}` valida | Se reutiliza y se extiende para auditorías y hechos. |
| Verificación | `answering.parse_and_validate`, `revalidate_claims`, `verification.verify`, `compose.render`, `abstention` | Se envuelve en un gate con política, hallazgos y persistencia. |
| Revisión humana | `reviews` con `original_output`, `human_output`, `reviewer_id`, versión optimista | Se usa para audits y hechos; se añade historial específico de hechos. |
| Trazabilidad | `model_runs` con `output`, `validation`, tokens, costo; `audit_logs`; CKP | Toda corrida y decisión queda registrada y exportable. |
| Agentes/skills | `agents`, `skills`, `builtin_agents.py`, `skills.load_config`, allowlist de tools | Nuevos agentes y skills de sistema, sin romper los existentes. |
| UI | Next.js 16.3.7, React 19, Tailwind 4, shadcn/Radix, sidebar responsive, tema claro/oscuro | Se añaden pestañas y paneles dentro del proceso, no un producto aparte. |

### 1.2 Brechas reales frente a los tres puntos

1. **No hay un gate único de publicación.** Hoy conviven grounding léxico, revalidación contra página/segmento, juez semántico y compositor. Cada capa mejora algo, pero no existe un objeto `answer_audit` que diga “esta respuesta se publicó porque pasó estas reglas” o “se bloqueó por estos hallazgos”.
2. **No hay verificación de normas jurídicas.** `legal_rules` existe, pero una afirmación del modelo sobre un artículo o una consecuencia jurídica no se contrasta obligatoriamente contra una norma oficial. Sin corpus, el modelo puede citar de memoria.
3. **No hay segunda opinión.** `verification.verify` usa un juez, pero no hay corrida independiente con otro modelo, comparación de claims, veredicto de acuerdo/desacuerdo, ni UI por proceso.
4. **`facts` no tiene posición de parte ni estado de prueba.** `facts.status` es epistémico (`ALLEGED`, `SUPPORTED`, `JUDICIALLY_DETERMINED`, etc.), no responde “qué alega el demandante, qué alega el demandado, qué está probado”. `fact_claims` y `evidence_links` ayudan, pero no hay ledger explícito ni historial.
5. **No hay política por proceso.** No se puede decir “esta demanda exige segunda opinión” o “este proceso bloquea conclusiones sin norma” sin tocar código.
6. **No hay exportación de auditorías/hechos.** El CKP exporta `facts` y `evidence`, pero no posiciones, revisiones, auditorías ni segunda opinión.
7. **La UI no tiene una superficie de verificación.** El chat muestra citas y límites, pero no un panel de auditoría, ni un tablero de hechos por parte, ni una comparación de modelos.

### 1.3 Restricciones que el plan respeta

- No se cambia el significado de `cases`; no se introduce un “caso global”.
- No se borran ni renombran tablas existentes.
- No se rompe el contrato de chat ni los cuatro casos cerrados.
- Toda tabla nueva es multitenant, con RLS, grants y trigger de misma organización.
- Las migraciones son aditivas y reversibles; el backfill es idempotente y por lotes.
- La segunda opinión y la reconstrucción de hechos son asíncronas; el chat no se bloquea.
- La UI conserva el sistema visual actual; no se introduce una paleta nueva ni un framework nuevo.
- Ningún modelo decide solo una consecuencia jurídica o un hecho probado.

---

## 2. Decisiones cerradas

Estas decisiones no quedan a discusión durante la implementación. Si un caso no se puede resolver, se aplica la regla de bloqueo indicada.

| ID | Decisión | Consecuencia |
|---|---|---|
| D1 | `cases` es el proceso/demanda canónico. | No hay tabla `demandas` paralela. N procesos ya funcionan por `case_id`. |
| D2 | Un proceso acumulado se modela como un `case` con `cuaderno`/`indice_numero`; no se parte en varios casos. | Se conserva la unidad procesal y la trazabilidad documental. |
| D3 | Toda entidad nueva lleva `organization_id` y `case_id`, salvo catálogos de organización. | RLS y aislamiento desde el diseño. |
| D4 | El gate legal es obligatorio para respuestas publicadas. | No existe respuesta de chat sin `answer_audit`; el estado puede ser `PASSED`, `WARNING`, `BLOCKED` o `NEEDS_REVIEW`. |
| D5 | Validación determinista primero; juez semántico después; segunda opinión es una capa separada. | El juez no puede “aprobar” una cifra, una parte o una norma que el determinista ya rechazó. |
| D6 | Una afirmación publicada necesita al menos una cita primaria (página o segmento), salvo respuesta determinista de ficha de partes. | Sin cita primaria, el claim se elimina o se convierte en límite. |
| D7 | `legal_rules` es la única fuente para afirmar una norma, artículo o consecuencia jurídica. | Si la norma no está verificada, la conclusión se bloquea; no se usa memoria del modelo. |
| D8 | Un hecho solo es `JUDICIALLY_PROVEN` con decisión judicial citada y documento fuente; `ADMITTED` exige admisión explícita de parte. | El LLM no puede convertir una alegación en hecho probado. |
| D9 | “Hechos probados” por defecto muestra solo `ADMITTED` y `JUDICIALLY_PROVEN`. | `EVIDENCE_SUPPORTED` y `DISPUTED` tienen pestañas propias. |
| D10 | La segunda opinión es manual por defecto; se puede activar automática por proceso para `ANALYSIS` o `ALL`. | No se dispara un gasto de modelo en cada mensaje sin decisión. |
| D11 | El modelo de segunda opinión debe ser distinto del respondedor; se exige distinto `model_id` y se recomienda distinto proveedor. | Si no hay modelo distinto configurado, la función queda deshabilitada con CTA; no hay fallback silencioso al mismo modelo. |
| D12 | La segunda opinión tiene fase independiente y fase de comparación. | El segundo modelo no ve la respuesta del primero en la fase 1. |
| D13 | La segunda opinión y la reconstrucción de hechos corren en Celery, cola `default`. | No bloquean el chat ni el request HTTP. |
| D14 | Los registros de auditoría y las revisiones de hechos son append-only. | No se actualiza un veredicto histórico; se crea una nueva corrida o revisión. |
| D15 | La revisión humana se registra en `reviews` y en `*_revisions`; un override exige motivo. | Se conserva la salida original y quién decidió. |
| D16 | La política por proceso vive en `case_verification_settings`; la política global en `legal_guard_policies`. | Ningún caso queda sin política: hereda la global. |
| D17 | Migraciones `0055` a `0061` (la `0062` la agrega P1 para el gate semántico); backfill y reindexado en scripts separados, no dentro de una migración larga. | Despliegue controlado, reversible y sin locks prolongados. |
| D18 | `chunks` no cambia de dimensión ni de modelo de embeddings; se agrega el tipo `fact` y metadatos. | No se rompe el índice pgvector existente. |
| D19 | El grafo se reconstruye después de cambios de hechos; no se escribe a mano. | Consistencia con `graph_build`. |
| D20 | El CKP sube a `1.1` e incluye auditorías, segunda opinión, posiciones y revisiones. | Exportación completa y reproducible. |
| D21 | La UI usa pestañas dentro de `/dashboard/procesos/[caseId]`; no se crea un módulo global por punto. | Menos ruido en el sidebar y navegación coherente. |
| D22 | Se conservan tokens, tipografía y componentes actuales; los colores de estado son semánticos, no decorativos. | Diseño consistente y accesible. |
| D23 | La feature se despliega con flags y se activa después de migrar, backfillear y verificar. | Rollback seguro. |
| D24 | Los umbrales de calidad se miden contra un golden set y no se aceptan regresiones. | No se declara “cerrado” por compilar. |
| D25 | La segunda opinión no puede escribir hechos, claims, evidencia ni documentos. | Su allowlist es de solo lectura. |
| D26 | Los hallazgos `CRITICAL` bloquean la publicación del claim afectado; si no queda ningún claim válido, la respuesta es `insufficient`. | No se publica una conclusión crítica sin soporte. |
| D27 | Un humano con `review.write` puede resolver un hallazgo, pero el override queda auditado y no borra el original. | Control legal y trazabilidad. |
| D28 | La UI no muestra un “porcentaje de verdad” único. Muestra conteos, hallazgos y citas. | Evita falsa precisión. |
| D29 | La latencia objetivo es: guard determinista p95 < 300 ms, juez semántico p95 < 5 s, segunda opinión p95 < 180 s. | Presupuestos medibles. |
| D30 | No se usa ningún dato real de producción en pruebas ni en prompts; los fixtures son sintéticos o ya autorizados. | Privacidad y cumplimiento. |

---

## 3. Modelo de dominio

### 3.1 Jerarquía

```text
Organización
└── Proceso / demanda (cases)
    ├── Carpetas (case_folders, jerárquicas)
    │   └── Documentos (documents) y archivos (case_files)
    ├── Medios (media) y transcripciones (transcript_segments)
    ├── Páginas OCR (document_pages)
    ├── Chunks (chunks) con embedding y metadata
    ├── Grafo (graph_nodes, graph_edges)
    ├── Claims (claims) y contradicciones (contradictions)
    ├── Hechos (facts), posiciones (fact_positions), evidencia (evidence, evidence_links)
    ├── Decisiones (decisions) y normas (legal_rules)
    ├── Citas (citations)
    ├── Respuestas y auditorías (answer_audits, answer_audit_*)
    └── Segunda opinión (second_opinion_runs, second_opinion_claims)
```

### 3.2 Regla de alcance

Toda consulta nueva usa `WHERE organization_id = current_org() AND case_id = :case_id` o pasa por `case_access`. Ningún endpoint acepta un `case_id` sin verificar membresía/rol. Los jobs reconstruyen un solo `case_id`. Los tests de N procesos crean dos casos con carpetas, documentos, hechos y auditorías y comprueban que no se mezclan.

### 3.3 Qué significa “proceso” y qué significa “demanda”

- `cases.case_number` es el radicado principal.
- `cases.external_reference` puede guardar el identificador del despacho o del sistema judicial.
- `documents.cuaderno`, `indice_numero`, `sub_orden` y `orden_procesal` conservan la estructura del expediente.
- Si una demanda tiene procesos acumulados, se mantiene un solo `case` y se distinguen los cuadernos; no se crean casos duplicados.
- La UI muestra “Proceso” o “Demanda” según el `jurisdiction`/tipo, pero la clave técnica es `case_id`.

---

## 4. Punto 1: módulo antialucinaciones legales

### 4.1 Objetivo operativo

Ninguna respuesta del chat se publica como afirmación si no cumple estas condiciones:

1. Cada afirmación de hecho tiene una cita primaria resoluble a una página o segmento del mismo proceso.
2. Cualquier cifra, monto, porcentaje, fecha o artículo citado aparece en el texto citado.
3. Cualquier nombre, parte o rol existe en la ficha de partes y coincide con el rol.
4. Cualquier norma o consecuencia jurídica está respaldada por una fila activa de `legal_rules` con jurisdicción, fuente, identificador y versión.
5. Cualquier hecho “determinado” tiene una decisión judicial citada.
6. Si hay dos versiones contradictorias, se muestran ambas o se declara que no hay determinación judicial.
7. Si no hay fuente suficiente, no se inventa: se elimina el claim, se muestra un límite y se registra el hallazgo.

### 4.2 Arquitectura del gate

```text
Pregunta
  -> política del caso (case_verification_settings + legal_guard_policies)
  -> recuperación de evidencia (RAG/agente actual)
  -> síntesis con claims y citas
  -> GATE LEGAL
       1. validación de citas y quote_hash
       2. grounding de cifras, fechas, montos y nombres
       3. validación de partes/roles
       4. validación de normas y jurisdicción
       5. validación de decisiones y contradicciones
       6. juez semántico (si la política lo pide)
       7. decisión: PASSED / WARNING / BLOCKED / NEEDS_REVIEW
  -> compositor: solo claims aprobados; límites explícitos
  -> persistencia de answer_audit + findings
  -> SSE: auditing, validating, done con audit_status
  -> UI: badge de auditoría y panel de hallazgos
```

### 4.3 Catálogo de reglas

| Código | Regla | Severidad | Acción |
|---|---|---|---|
| `CITATION_MISSING` | Claim sin cita primaria | CRITICAL | Eliminar claim; si no quedan, `insufficient` |
| `CITATION_UNRESOLVED` | Handle no existe o apunta a otro caso | CRITICAL | Eliminar claim |
| `CITATION_STALE` | Documento/página eliminada o versión inválida | CRITICAL | Eliminar claim; marcar fuente |
| `QUOTE_HASH_MISMATCH` | El extracto no coincide con la fuente | CRITICAL | Eliminar claim |
| `LOW_OCR_CONFIDENCE` | Fuente bajo el umbral de OCR | HIGH | Mantener solo como límite o revisión |
| `LOW_ASR_CONFIDENCE` | Segmento bajo el umbral o hablante no resuelto | HIGH | Mantener como límite; no probar hecho |
| `NUMBER_MISMATCH` | Cifra/monto/porcentaje no aparece en la cita | CRITICAL | Eliminar claim |
| `DATE_MISMATCH` | Fecha no aparece o no coincide | CRITICAL | Eliminar claim |
| `PARTY_MISMATCH` | Nombre/rol no existe o no corresponde | CRITICAL | Eliminar claim |
| `LEGAL_RULE_MISSING` | Norma/artículo no verificado en `legal_rules` | CRITICAL | Bloquear conclusión jurídica |
| `LEGAL_RULE_STALE` | Versión de norma anterior al hecho sin justificación | HIGH | Revisión humana |
| `JURISDICTION_MISMATCH` | Norma de otra jurisdicción | CRITICAL | Bloquear |
| `DECISION_MISSING` | Se afirma “probado/determinado” sin decisión | CRITICAL | Degradar a alegación o eliminar |
| `CONTRADICTION_ONE_SIDE` | Hay dos versiones y se publica una sola | CRITICAL | Mostrar ambas o bloquear |
| `UNSUPPORTED_LEGAL_CONCLUSION` | Consecuencia jurídica sin norma y decisión | CRITICAL | Bloquear o reformular |
| `UNSUPPORTED_INFERENCE` | Inferencia no contenida en la fuente | HIGH | Mover a límite |
| `ANSWER_TOO_CERTAIN` | “Es un hecho” sobre material alegado | HIGH | Reformular |
| `SECOND_OPINION_DISAGREES` | El segundo modelo discrepa en un claim crítico | HIGH | Revisión humana |
| `AUDIT_MODEL_ERROR` | Juez/segundo modelo no disponible | MEDIUM | Reintentar; no bloquea si el determinista pasó |

### 4.4 Servicio `legal_guard`

Nuevo `apps/api/app/services/legal_guard.py`:

- `load_policy(conn, case_id)`: resuelve `case_verification_settings` y `legal_guard_policies`; si no hay, usa la política global `is_default`.
- `audit_answer(conn, case_id, question, answer, claims, evidence, citations, *, actor_id, policy) -> AuditResult`.
- `validate_citation`: reutiliza `services.citations.validate` y exige `(document_id,page)` o `(media_id,segment_id)` del mismo caso.
- `validate_numbers_dates`: extrae números/fechas del claim y de la cita; normaliza moneda, separadores y fechas; exige coincidencia exacta; no usa tolerancia para cifras jurídicas.
- `validate_names_parties`: normaliza nombres, alias y roles; exige que la parte exista en `parties` y que el rol coincida; no acepta nombres del prompt.
- `validate_legal_rules`: busca en `legal_rules` por `jurisdiction`, `source`, `identifier` y `version_date`; exige fila activa y versión aplicable; si no hay, `LEGAL_RULE_MISSING`.
- `validate_decision`: si el claim es `JUDICIALLY_DETERMINED`, exige `determined_by_decision_id` y `decision.source_document_id`; si no, `DECISION_MISSING`.
- `validate_contradictions`: cruza `contradictions` y `claims` citados; si una de las dos versiones está en la respuesta, exige la otra o un límite.
- `validate_party_positions`: para hechos, verifica `fact_positions` y `evidence_links`; no confunde “alega” con “prueba”.
- `decide`: agrega hallazgos por severidad; `CRITICAL` bloquea el claim; `HIGH` permite solo con límite; `MEDIUM` genera warning; `LOW` se registra.
- `persist_audit`: inserta `answer_audits`, `answer_audit_claims`, `answer_audit_citations`, `answer_audit_findings`; idempotente por `(case_id, chat_message_id, answer_hash, policy_version)`.
- `render_limits`: devuelve límites legibles para el compositor.

### 4.5 Integración con el chat

- `routers/query.py` llama al gate después de `parse_and_validate` y antes de `compose.render`.
- Si el gate devuelve `BLOCKED` y no hay claims aprobados, se devuelve `insufficient` con `audit_status=BLOCKED` y la lista de búsquedas realizadas.
- Si quedan claims, se compone solo con esos; los bloqueados se agregan a `uncertainties` con su código y fuente.
- `routers/chats.py` guarda `answer_audits` y añade `audit_id`, `audit_status`, `audit_findings_count` al mensaje asistente.
- SSE emite `auditing` con el número de claims y, al terminar, `done` con `audit_status`, `audit_id` y `findings`.
- El `ChatWidget` muestra un badge `Verificado`, `Advertencia`, `Bloqueado` o `Sin auditar` y abre el panel de auditoría.
- El `POST /query` directo también pasa por el gate; no hay una puerta trasera.

### 4.6 Modelo de datos del punto 1

#### `legal_guard_policies`
- `id uuid pk`
- `organization_id uuid not null`
- `case_id uuid null` (null = política global de la organización)
- `name text not null`
- `mode text not null check (mode in ('STRICT','BALANCED','ADVISORY'))`
- `require_primary_citation bool not null default true`
- `require_legal_norm bool not null default true`
- `block_on_unsupported bool not null default true`
- `block_on_numbers bool not null default true`
- `block_on_parties bool not null default true`
- `block_on_legal_conclusion bool not null default true`
- `require_second_opinion bool not null default false`
- `second_opinion_model_id uuid null references ai_models(id)`
- `max_unsupported_claims int not null default 0`
- `min_ocr_confidence numeric(4,3) not null default 0.55`
- `min_asr_confidence numeric(4,3) not null default 0.55`
- `allowed_jurisdictions text[] not null default '{}'`
- `version int not null default 1`
- `is_default bool not null default false`
- `created_by uuid references users(id)`
- `created_at/updated_at timestamptz not null default now()`
- Índice único parcial: una política global por org (`WHERE case_id IS NULL AND is_default`), una política por caso y nombre.

#### `case_verification_settings`
- `case_id uuid pk references cases(id) on delete cascade`
- `organization_id uuid not null`
- `policy_id uuid not null references legal_guard_policies(id)`
- `second_opinion_enabled bool not null default false`
- `second_opinion_model_id uuid null references ai_models(id)`
- `auto_second_opinion text not null default 'NEVER' check (in ('NEVER','ANALYSIS','ALL','STRICT_ONLY'))`
- `updated_by uuid references users(id)`, `updated_at`
- Trigger `enforce_same_org` y RLS.

#### `answer_audits`
- `id uuid pk`
- `organization_id`, `case_id`
- `chat_message_id uuid null references chat_messages(id) on delete set null`
- `model_run_id uuid null references model_runs(id) on delete set null`
- `policy_id uuid references legal_guard_policies(id)`
- `question text not null`
- `answer_hash char(64) not null`
- `answer_excerpt text`
- `status text not null check (in ('PASSED','WARNING','BLOCKED','NEEDS_REVIEW','ERROR'))`
- `mode text not null`
- `claims_total int not null default 0`
- `claims_supported int not null default 0`
- `claims_blocked int not null default 0`
- `findings_critical int not null default 0`
- `evidence_hash char(64)`
- `evidence_snapshot jsonb not null default '[]'` (handles, fuente, texto truncado, hash)
- `details jsonb not null default '{}'`
- `pipeline_version text not null`
- `prompt_version text`
- `duration_ms int`
- `idempotency_key char(64)`
- `created_by uuid references users(id)`
- `created_at timestamptz not null default now()`
- La revisión humana vive en `reviews` con `entity_type='answer_audit'`; `answer_audits` es append-only y no se actualiza para “aprobar” un veredicto histórico.
- Índices: `(case_id, created_at desc)`, `(chat_message_id)`, `(status)`, `(policy_id)`, único `(organization_id, idempotency_key)`.

#### `answer_audit_claims`
- `id uuid pk`, `audit_id uuid not null references answer_audits(id) on delete cascade`
- `claim_index int not null`, `claim_text text not null`, `claim_hash char(64) not null`
- `claim_type text` (`FACT`,`LEGAL_CONCLUSION`,`PROCEDURAL`,`PARTY_ALLEGATION`,`INFERENCE`)
- `citation_handles text[] not null default '{}'`
- `status text not null check (in ('SUPPORTED','UNSUPPORTED','CONTRADICTED','NEEDS_REVIEW'))`
- `grounding_score numeric(6,5)`
- `missing_numbers text[]`, `missing_dates text[]`, `missing_parties text[]`
- `legal_rule_ids uuid[]`
- `reasons jsonb not null default '[]'`
- `auditor text not null default 'DETERMINISTIC' check (in ('DETERMINISTIC','JUDGE'))`; la segunda opinión usa `second_opinion_claims`.
- único `(audit_id, claim_index, auditor)`

#### `answer_audit_citations`
- `id`, `audit_id`, `citation_id uuid`, `valid bool`, `reasons text[]`, `source_type`, `source_id`, `page_number`, `segment_id`, `quote_hash`, `created_at`.
- Índices por `audit_id`, `citation_id`.

#### `answer_audit_findings`
- `id`, `audit_id` (la segunda opinión tiene su propia tabla `second_opinion_findings`)
- `claim_index int null`
- `code text not null` (catálogo de 4.3)
- `severity text not null check (in ('CRITICAL','HIGH','MEDIUM','LOW'))`
- `message text not null`
- `source_type/source_id`
- `blocking bool not null default false`
- `metadata jsonb not null default '{}'`
- `created_at`
- Índices `(audit_id, severity)`, `(code)`.

#### `legal_rule_citations`
- `id`, `organization_id`, `case_id`, `legal_rule_id`, `target_type`, `target_id`, `quote text`, `version_date`, `created_at`.
- Permite citar una norma oficial sin cambiar el CHECK de `citations.source_type`.

#### `legal_rules` (extensión)
- Añadir `official_url text`, `checksum char(64)`, `verified_at timestamptz`, `verified_by uuid`, `is_active bool default true`, `metadata jsonb default '{}'`.
- Índice único por `(organization_id, jurisdiction, source, identifier, version_date)`.
- No se permite afirmar una norma sin fila activa.

**Corpus inicial del piloto:** `data/legal_rules/co/` con un JSON por norma y `checksums.json`. El agregado `checksums.json` existe y está validado (143 normas, 29 fuentes); los checksums por fila siguen siendo la fuente primaria. Incluye, como mínimo, las normas que ya citan los casos piloto: Código Civil, Código General del Proceso (Ley 1564 de 2012) y las normas sustantivas/procesales que aparezcan en los escritos. Cada fila lleva `source` oficial, `identifier`, `version_date`, `official_url`, `checksum` y `text`. El importador `scripts/import_legal_rules.py` valida checksum, hace `--dry-run` y no descarga nada en runtime. Si una norma no está en el corpus, el guard bloquea la conclusión jurídica; no se completa con memoria del modelo. El corpus se amplía por proceso con revisión legal, no por scraping automático.

### 4.7 Política de publicación

- `STRICT`: cualquier `CRITICAL` bloquea el claim; una respuesta sin claims aprobados es `insufficient`; segunda opinión obligatoria si la política lo pide.
- `BALANCED`: `CRITICAL` bloquea; `HIGH` se muestra como límite; `MEDIUM` warning.
- `ADVISORY`: no bloquea, pero registra todo y muestra el badge; se usa solo en borradores internos, nunca en una respuesta marcada como “verificada”.
- El estado por defecto de un caso nuevo es `STRICT` para conclusiones jurídicas y `BALANCED` para resúmenes exploratorios; la política global define el default.

### 4.8 Criterios de aceptación del punto 1

- [ ] Toda respuesta nueva asistente tiene una fila en `answer_audits`; las históricas se auditan bajo demanda.
- [ ] El 100% de los claims publicados nuevos tienen cita primaria válida en el golden set.
- [ ] 0 cifras, fechas, montos, partes o normas inventadas en el golden set.
- [ ] 0 claims con `JUDICIALLY_DETERMINED` sin decisión.
- [ ] Toda contradicción publicada muestra las dos versiones o un límite.
- [ ] Un `CRITICAL` no aparece como hecho en la respuesta final.
- [ ] El panel de auditoría muestra código, severidad, claim, fuente y acción.
- [ ] El override humano queda en `reviews` y conserva el original.
- [ ] Guard determinista p95 < 300 ms; juez p95 < 5 s.
- [ ] No hay regresión en los cuatro casos cerrados.

---

## 5. Punto 2: segunda opinión por proceso/demanda

### 5.1 Objetivo

Permitir que, por proceso y bajo demanda, otro modelo audite una respuesta con una **respuesta independiente** y una **comparación** estructurada. El usuario ve si ambos modelos coinciden, en qué difieren y qué claims requieren revisión humana.

### 5.2 Configuración

- `case_verification_settings.second_opinion_enabled`.
- `second_opinion_model_id`: modelo distinto del respondedor.
- `auto_second_opinion`: `NEVER` (default), `ANALYSIS`, `ALL`, `STRICT_ONLY`.
- `legal_guard_policies.require_second_opinion`: obliga a solicitarla antes de publicar en modo `STRICT`.
- Validación: el modelo debe existir, estar activo, pertenecer a la organización y ser distinto del `model_id` del mensaje. Si no, `SECOND_OPINION_MODEL_REQUIRED` o `SECOND_OPINION_MODEL_MUST_DIFFER`.
- Si no hay modelo configurado, la UI muestra “Configura un modelo de segunda opinión” y no hay fallback al mismo modelo.
- **Default del seed:** `data/modelos/segunda_opinion.json` declara pares elegibles `gemini/gemini-flash-latest` y `gemini/gemini-flash-lite-latest`. El seed activa el primero cuyo proveedor y modelo sean distintos del respondedor. Si ninguno está disponible, la función queda deshabilitada con CTA; no se ejecuta una segunda opinión con el mismo modelo.
- **Prerrequisito:** `POST /second-opinion` exige una `answer_audit` de la respuesta; si no existe, ejecuta primero el gate legal y solo luego encola. Si el gate falla por configuración, devuelve 422 con el código.

### 5.3 Flujo

```text
Usuario pulsa "Solicitar segunda opinión"
  -> se crea second_opinion_run QUEUED
  -> worker toma el run
  -> fase 1: modelo verificador independiente
       input: pregunta + evidencia primaria + política
       NO input: respuesta del respondedor
       output: claims, citas, incertidumbres, límites
  -> fase 2: comparación
       input: respuesta original + claims del verificador + evidencia
       output: AGREE / PARTIAL / DISAGREE / INCONCLUSIVE
       diferencias por claim, cifra, parte, norma, cita
  -> se persiste run, claims y findings
  -> UI muestra lado a lado y badge
  -> si hay desacuerdo crítico: NEEDS_REVIEW
```

### 5.4 Reglas de independencia

- El prompt de la fase 1 no incluye la respuesta original, sus claims ni su razonamiento.
- La evidencia de la fase 1 es la misma que sustenta la respuesta original, en un snapshot reproducible; si falta, el worker vuelve a recuperar con la pregunta y el mismo `case_id`.
- La fase 2 sí ve ambas respuestas para comparar.
- Se registra `prompt_id`, `prompt_version`, `model_run_id`, `input_hash`, tokens, costo y latencia.
- El segundo modelo no tiene tools de escritura. Allowlist: `search_case`, `read_document`, `get_document_page`, `find_person`, `graph_query`, `get_claims`, `get_fact`, `verify_citation`, `lookup_legal_rule`.
- Si el segundo modelo intenta una tool no permitida, se rechaza y se registra el hallazgo; no se ejecuta.

### 5.5 Modelo de datos del punto 2

#### `second_opinion_runs`
- `id uuid pk`, `organization_id`, `case_id`
- `audit_id uuid references answer_audits(id)`
- `chat_message_id uuid references chat_messages(id)`
- `responder_model_run_id uuid references model_runs(id)`
- `verifier_model_id uuid references ai_models(id)`
- `verifier_provider text`, `verifier_model_name text`
- `status text check (in ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED'))`
- `verdict text check (in ('AGREE','PARTIAL','DISAGREE','INCONCLUSIVE'))`
- `summary text`
- `output jsonb not null default '{}'`
- `model_run_id uuid references model_runs(id)`
- `tokens_in int`, `tokens_out int`, `cost numeric(12,6)`, `duration_ms int`
- `error_code text`, `error_detail text`
- `idempotency_key char(64)`, `created_by`, `created_at`, `started_at`, `completed_at`
- Índices `(case_id, created_at desc)`, `(status)`, `(audit_id)`, único `(organization_id, idempotency_key)`.

#### `second_opinion_claims`
- `id`, `run_id`, `claim_index`, `claim_text`, `claim_hash`
- `citations jsonb`, `status text`, `comparison text` (`MATCH`,`MISSING`,`EXTRA`,`CONTRADICTED`,`UNCERTAIN`)
- `reasons jsonb`, `created_at`
- único `(run_id, claim_index)`.

#### `second_opinion_findings`
- `id`, `run_id`, `claim_index`, `code`, `severity`, `message`, `metadata`, `created_at`.
- Códigos: `MODEL_DISAGREEMENT`, `MISSING_CITATION`, `EXTRA_CLAIM`, `DIFFERENT_NUMBER`, `DIFFERENT_PARTY`, `DIFFERENT_LEGAL_RULE`, `DIFFERENT_CONTRADICTION`, `INCONCLUSIVE`.

### 5.6 API

- `POST /cases/{case_id}/messages/{message_id}/second-opinion` (`second_opinion.run`): crea/reutiliza run.
- `GET /cases/{case_id}/second-opinions?status=&limit=` (`second_opinion.read`).
- `GET /cases/{case_id}/second-opinions/{run_id}`.
- `POST /cases/{case_id}/second-opinions/{run_id}/cancel`.
- `POST /cases/{case_id}/second-opinions/{run_id}/review` (`review.write`).
- El `GET` de mensajes incluye `second_opinion_status` y `second_opinion_run_id`.

### 5.7 UI

- En el panel de auditoría de una respuesta: botón `Solicitar segunda opinión`.
- Estado: `Pendiente`, `Analizando`, `Completada`, `Falló`, `Cancelada`.
- Comparación:
  - Columna izquierda: respuesta original.
  - Columna derecha: respuesta independiente.
  - Debajo: tabla de claims con `Coincide`, `Falta`, `Sobra`, `Contradice`, `Incierto`.
  - Hallazgos críticos y CTA `Revisar`.
- En móvil: las columnas se apilan; el veredicto y los hallazgos van primero.
- No se muestra un porcentaje de “verdad”; se muestra el veredicto y los conteos.

### 5.8 Criterios de aceptación del punto 2

- [ ] Se puede solicitar por respuesta y por proceso.
- [ ] El modelo es distinto; si no, la API devuelve error de configuración y la UI no permite ejecutar.
- [ ] La fase 1 no recibe la respuesta original.
- [ ] La corrida es asíncrona, idempotente y cancelable.
- [ ] Persiste output, tokens, costo, latencia y errores.
- [ ] La UI muestra lado a lado y diferencias.
- [ ] Un desacuerdo crítico genera `NEEDS_REVIEW`.
- [ ] p95 < 180 s; costo por corrida registrado y acotado por `max_llm_tokens`.
- [ ] No hay tools de escritura en la allowlist.
- [ ] Prueba con dos procesos: cada run queda aislado por `case_id`.

---

## 6. Punto 3: hechos de demandante y demandado

### 6.1 Objetivo

Construir un ledger de hechos del proceso que responda, para cada hecho:

- qué se afirma;
- quién lo afirma (demandante, demandado, tercero);
- si lo niega, lo admite o guarda silencio;
- qué evidencia lo soporta o refuta;
- si hay decisión judicial;
- si está probado, solo con prueba admisible o decisión.

### 6.2 Taxonomía de prueba

| Estado | Significado | ¿Aparece en “Probados”? |
|---|---|---|
| `ALLEGED` | Solo fue alegado, sin prueba | No |
| `ADMITTED` | Una parte lo admitió expresamente o no lo disputó de forma válida | Sí |
| `EVIDENCE_SUPPORTED` | Hay evidencia admisible que lo soporta, pero no hay decisión ni admisión | No, pestaña “Con prueba” |
| `JUDICIALLY_PROVEN` | Una decisión judicial lo determinó, con documento fuente | Sí |
| `DISPUTED` | Las partes discrepan o hay evidencia en ambos sentidos | No, pestaña “Discutidos” |
| `REJECTED` | La decisión o la evidencia lo descartó | No |
| `UNRESOLVED` | Falta información o hay contradicción no resuelta | No |

Regla dura: `JUDICIALLY_PROVEN` exige `proven_by_decision_id` (o `determined_by_decision_id`) y una decisión con `source_document_id`; `ADMITTED` exige una `fact_position` con `position='ADMITS'`. El modelo no puede escribir esos estados por sí solo.

### 6.3 Posiciones de parte

`fact_positions` registra, por hecho y parte:

- `side`: `DEMANDANTE`, `DEMANDADO`, `TERCERO`, `NO_ATRIBUIDO`.
- `position`: `ALLEGES`, `DENIES`, `ADMITS`, `UNKNOWN`.
- `source_claim_id`: claim que sostiene la posición.
- `source_citation_id`: página/segmento exacto.
- `confidence`.
- `created_by`, `created_at`, `updated_at`.

La UI muestra dos columnas: Demandante y Demandado. Un hecho común puede tener posición en ambas; un hecho sin posición se muestra como “Común / no atribuido” y no se marca probado.

### 6.4 Extracción y revisión

1. **Candidatos**: el agente `Analista de hechos` recibe páginas/segmentos y claims; devuelve hechos candidatos con `party_side`, `position`, `claim_ids`, `citation_handles` y `legal_issue`.
2. **Validación**: cada hecho debe tener al menos una cita primaria o un claim con cita; si no, se descarta.
3. **Deduplicación**: por proposición normalizada + parte; se fusionan citas y posiciones.
4. **Persistencia**: `facts` con `proof_status='ALLEGED'`; `fact_positions`; `fact_claims`; `citations` con `target_type='fact'`.
5. **Hechos de decisión**: `legal_extraction._insert_facts` debe escribir `proof_status='JUDICIALLY_PROVEN'` y `proof_basis='JUDICIAL_DECISION'` cuando el hecho venga de una decisión con `determined_by_decision_id`; no se deja el default `ALLEGED`.
6. **Derivación de prueba**: servicio `facts.proof` aplica la taxonomía; nunca asigna `JUDICIALLY_PROVEN` sin decisión.
7. **Revisión humana**: el revisor puede aceptar, editar, rechazar o marcar; cada cambio crea `fact_revisions` y un `reviews`.
8. **Reindexado**: `chunk_facts` + `index_structured`; grafo.
9. **Publicación**: la UI solo muestra como “probados” los estados permitidos; el resto queda en pestañas separadas.

### 6.5 Modelo de datos del punto 3

#### Extensión de `facts`
- `proof_status text not null default 'ALLEGED'` con CHECK de la taxonomía.
- `proof_basis text` (`NONE`,`ADMISSION`,`EVIDENCE`,`JUDICIAL_DECISION`).
- `proven_by_decision_id uuid references decisions(id)`.
- `proven_at timestamptz`, `proven_by uuid references users(id)`.
- `issue_id uuid references issues(id)`.
- `fact_code text` (código legible por proceso).
- `created_at timestamptz not null default now()`.
- `is_proven boolean generated always as (proof_status in ('ADMITTED','JUDICIALLY_PROVEN')) stored`.
- CHECK: `proof_status <> 'JUDICIALLY_PROVEN' OR coalesce(proven_by_decision_id, determined_by_decision_id) IS NOT NULL`.
- Se conserva `status` para el estado epistémico; `proof_status` es el estado de publicación.

#### `fact_positions`
- `id uuid pk`, `organization_id`, `case_id`
- `fact_id uuid not null references facts(id) on delete cascade`
- `party_id uuid null references parties(id) on delete set null`
- `side text not null check (in ('DEMANDANTE','DEMANDADO','TERCERO','NO_ATRIBUIDO'))`
- `position text not null check (in ('ALLEGES','DENIES','ADMITS','UNKNOWN'))`
- `source_claim_id uuid null references claims(id) on delete set null`
- `source_citation_id uuid null references citations(id) on delete set null`
- `confidence numeric(4,3)`
- `created_by uuid`, `created_at`, `updated_at`
- único `(fact_id, party_id)` (con `party_id` null, usar índice parcial por hecho y `side` si se requiere).

#### `fact_revisions`
- `id`, `organization_id`, `case_id`, `fact_id`
- `version int not null`
- `proposition text not null`
- `proof_status text not null`
- `proof_basis text`
- `proven_by_decision_id uuid`
- `positions jsonb not null default '[]'`
- `evidence_ids uuid[] not null default '{}'`
- `citations jsonb not null default '[]'`
- `changed_by uuid references users(id)`
- `reason text not null`
- `created_at timestamptz not null default now()`
- único `(fact_id, version)`; append-only.

#### `evidence_links` (extensión)
- `citation_id uuid references citations(id)`.
- `added_by uuid references users(id)`, `added_at timestamptz`.
- La evidencia debe ser del mismo caso; trigger de misma organización.
- Solo evidencia con `admissibility_status='admitted'` puede sostener `ADMITTED`/`JUDICIALLY_PROVEN`; `unknown` puede ser `EVIDENCE_SUPPORTED` con límite.

#### `decisions` (sin cambio estructural)
- Se usa `source_document_id`, `decision_date`, `outcome`, `reasoning`.
- Se añade índice `(case_id, decision_date)`.
- `fact` `JUDICIALLY_PROVEN` debe apuntar a una decisión con `source_document_id`.

### 6.6 Graph y pgvector

#### Grafo
- Nodo `Fact` con metadata `proof_status`, `is_proven`, `fact_code`.
- Nodos `Party` existentes.
- Aristas nuevas:
  - `PARTY_ALLEGES_FACT`
  - `PARTY_DENIES_FACT`
  - `PARTY_ADMITS_FACT`
  - `EVIDENCE_SUPPORTS_FACT`
  - `EVIDENCE_REFUTES_FACT`
  - `DECISION_PROVES_FACT`
  - `FACT_ABOUT_ISSUE`
- Se conservan `CLAIM ASSERTS/REFUTES FACT`, `EVIDENCE SUPPORTS/REFUTES FACT`, `DECISION DECIDES FACT`.
- `graph.build_case_graph` se actualiza para leer `fact_positions` y `proof_status`.
- Al cambiar `facts`, `fact_positions`, `evidence_links` o `decisions`, se encola `graph_build` (misma ventana de deduplicación que hoy).

#### Chunks y pgvector
- `chunk_facts` cambia a `chunk_type='fact'` y metadata:
  - `proof_status`, `is_proven`, `fact_code`, `party_positions` (side/position), `legal_issue`, `citation_handles`, `decision_id`.
- `index_structured` incluye `fact` en el borrado/creación; no cambia la dimensión del embedding.
- `chunks.metadata` con índice GIN `jsonb_path_ops` para filtros por estado/parte.
- Índice `(case_id, chunk_type)` para reindexados y consultas.
- Reindexado: `index_structured` + `index_case` después del backfill; invalidar `retrieval:{case_id}`.
- La respuesta del chat no cita un chunk estructurado como fuente primaria: lo usa para recuperar y luego resuelve a página/segmento.

### 6.7 UI de hechos

- Pestaña `Hechos` dentro del proceso.
- Segmentos: `Probados` (default), `Con prueba`, `Discutidos`, `Todos`.
- Toggle de lado: `Demandante`, `Demandado`, `Ambos`.
- Filtros: parte, issue, estado, texto.
- Tarjeta/row:
  - proposición;
  - `ProofBadge` con texto e icono, no solo color;
  - dos columnas de posición (Demandante / Demandado) con `Alega`, `Niega`, `Admite`, `Sin posición`;
  - chips de fuente `documento p. X` / `audio min. MM:SS`;
  - acción `Ver evidencia`, `Revisar`.
- Detalle:
  - estado, base de prueba, decisión;
  - matriz evidencia (soporta/refuta/neutral);
  - citas;
  - posiciones;
  - historial de revisiones;
  - enlaces al grafo;
  - acciones de revisión.
- Estado vacío: “Aún no hay hechos extraídos” + botón `Reconstruir hechos` (requiere documentos/claims).
- No hay acción “marcar todos como probados”.

### 6.8 Criterios de aceptación del punto 3

- [ ] Cada hecho tiene `case_id`, proposición y al menos una fuente o posición de parte.
- [ ] `JUDICIALLY_PROVEN` solo con decisión y documento fuente.
- [ ] `ADMITTED` solo con admisión explícita.
- [ ] `EVIDENCE_SUPPORTED` nunca aparece en “Probados”.
- [ ] `fact_positions` muestra demandante y demandado en la UI.
- [ ] Cada cambio crea `fact_revisions` y `reviews`.
- [ ] El grafo incluye hechos, posiciones y decisiones.
- [ ] Los chunks `fact` se reindexan y recuperan.
- [ ] El CKP incluye hechos, posiciones, revisiones y auditorías.
- [ ] Prueba con dos procesos: no se mezclan hechos ni posiciones.

---

## 7. Plan de base de datos, pgvector, grafo y migración

### 7.1 Secuencia de migraciones

| Migración | Contenido | Reversible |
|---|---|---|
| `0055_legal_guard` | `legal_guard_policies`, `case_verification_settings`, `answer_audits`, `answer_audit_claims`, `answer_audit_citations`, `answer_audit_findings`, `legal_rule_citations`; extensión de `legal_rules` | Sí: drop de tablas/columnas nuevas |
| `0056_second_opinion` | `second_opinion_runs`, `second_opinion_claims`, `second_opinion_findings`; FKs a `answer_audits`, `model_runs`, `ai_models` | Sí: drop de tablas |
| `0057_party_facts` | columnas de `facts`, `fact_positions`, `fact_revisions`, extensión de `evidence_links`; checks e índices | Sí: drop de columnas/tablas |
| `0058_facts_graph_indexes` | RLS, grants, triggers de misma organización, índices GIN/btree, índices de grafo y chunks; defaults | Sí: drop de índices/policies/triggers |
| `0059_legal_modules_purge` | `DELETE` para `purge_case` en las tablas append-only, con la transacción de purga | Sí: revoke |
| `0060_graph_fact_edges` | Tipos de arista `POSITION_ON`, `DECISION_PROVES_FACT`, `FACT_ABOUT_ISSUE` | Sí: restaurar el CHECK anterior |
| `0061_second_opinion_worker` | `UPDATE` de `second_opinion_runs` para el worker | Sí: revoke |
| `0062_legal_gate_semantics` (P1, en curso) | Precios de modelo, `cost_source`/`comparison_mode` y trigger de `EVIDENCE_SUPPORTED` | Sí: drop de columnas/trigger |

La `0062` no está aplicada en una base limpia al cierre de esta pasada: el
cargador de migraciones parte el cuerpo de la función en el primer `$$`. Es un
bloqueo de P1, no de estos módulos ya migrados.

**Backfill y reindexado no van dentro de una migración larga.** Se ejecutan con scripts idempotentes después de `alembic upgrade head`:
- `scripts/backfill_legal_modules.py`
- `scripts/reindex_facts.py`
- `scripts/rebuild_graphs.py` (si se requiere por lote)
- `scripts/import_legal_rules.py`

### 7.2 RLS, grants y triggers

Para cada tabla nueva:
```sql
ALTER TABLE <tabla> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <tabla> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <tabla>
  USING (organization_id = current_org())
  WITH CHECK (organization_id = current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON <tabla> TO "{{DB_APP_USER}}";
```
- `answer_audits`, `answer_audit_claims`, `answer_audit_citations`, `answer_audit_findings`, `fact_revisions`, `second_opinion_runs`, `second_opinion_claims`, `second_opinion_findings` son **append-only** para el rol de aplicación: `GRANT SELECT, INSERT`; las correcciones se hacen con nuevas filas.
- Triggers `enforce_same_org` para cada FK lógica (`case_id`, `fact_id`, `audit_id`, `party_id`, `citation_id`, `model_run_id`).
- Trigger `trg_fact_proof_invariant` (`CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED`) valida al cerrar la transacción:
  - `JUDICIALLY_PROVEN` exige `proven_by_decision_id`/`determined_by_decision_id` y una decisión con `source_document_id`;
  - `ADMITTED` exige al menos una `fact_position` con `position='ADMITS'`;
  - `EVIDENCE_SUPPORTED` exige al menos una `evidence_link` con `stance='supports'`;
  - `REJECTED` exige decisión o evidencia `admissibility_status='rejected'`.
- Un job de reconciliación (`facts.reconcile`) detecta hechos cuyo `proof_status` no cumple el invariante y los marca `NEEDS_REVIEW`; no los borra. También recalcula `fact_positions.side` si cambió el rol de una parte y encola el grafo.
- `fact_positions` admite UPDATE porque la posición de parte puede corregirse; cada cambio crea `fact_revisions` y `reviews`.
- `answer_audits`, `answer_audit_claims`, `answer_audit_citations`, `answer_audit_findings` son append-only; la revisión se guarda en `reviews` y en una nueva corrida si hace falta.
- `legal_guard_policies` y `case_verification_settings` tienen UPDATE normal; auditadas.

### 7.3 Índices mínimos

```sql
-- Auditoría
CREATE INDEX ix_answer_audits_case_created ON answer_audits(case_id, created_at DESC);
CREATE INDEX ix_answer_audits_message ON answer_audits(chat_message_id);
CREATE INDEX ix_answer_audits_status ON answer_audits(status);
CREATE INDEX ix_answer_audit_findings_audit_sev ON answer_audit_findings(audit_id, severity);
CREATE UNIQUE INDEX ux_answer_audits_idem ON answer_audits(organization_id, idempotency_key);

-- Segunda opinión
CREATE INDEX ix_second_opinion_case_created ON second_opinion_runs(case_id, created_at DESC);
CREATE INDEX ix_second_opinion_status ON second_opinion_runs(status);
CREATE UNIQUE INDEX ux_second_opinion_idem ON second_opinion_runs(organization_id, idempotency_key);

-- Hechos
CREATE INDEX ix_facts_case_proof ON facts(case_id, proof_status);
CREATE INDEX ix_facts_case_proven ON facts(case_id, is_proven) WHERE is_proven;
CREATE INDEX ix_fact_positions_case_party ON fact_positions(case_id, party_id);
CREATE INDEX ix_fact_positions_fact ON fact_positions(fact_id);
CREATE INDEX ix_fact_revisions_fact_version ON fact_revisions(fact_id, version);

-- Chunks
CREATE INDEX ix_chunks_metadata_gin ON chunks USING gin (metadata jsonb_path_ops);
CREATE INDEX ix_chunks_case_type ON chunks(case_id, chunk_type);

-- Grafo
CREATE INDEX ix_graph_edges_case_type_target ON graph_edges(case_id, edge_type, target_node_id);
```

### 7.4 Backfill de hechos

Orden exacto:
1. `proof_status`:
   - `JUDICIALLY_DETERMINED` -> `JUDICIALLY_PROVEN`, `proof_basis='JUDICIAL_DECISION'`.
   - `SUPPORTED` -> `EVIDENCE_SUPPORTED`, `proof_basis='EVIDENCE'`.
   - `CONTRADICTED`/`DISPUTED` -> `DISPUTED`.
   - `ALLEGED` -> `ALLEGED`.
   - `UNRESOLVED` -> `UNRESOLVED`.
2. `fact_positions` desde `fact_claims` + `claims.claimant_party_id`:
   - `stance='asserts'` -> `ALLEGES`.
   - `stance='disputes'` -> `DENIES`.
   - Si no hay parte, `NO_ATRIBUIDO`.
   - Side desde `parties.role`: `claimant/plaintiff/ejecutante` -> `DEMANDANTE`; `defendant/respondent/ejecutado` -> `DEMANDADO`; resto -> `TERCERO`.
3. `is_proven` generado; no se escribe a mano.
4. `fact_revisions` versión 1 para cada hecho, con snapshot de posiciones y evidencia.
5. `chunks` `fact` se regeneran en el reindexado.
6. `graph_build` por caso.
7. Invalidar caché de retrieval y respuestas; subir `PIPELINE_VERSION`/`ANSWER_CACHE_VERSION`.

**Idempotencia:** cada script calcula un hash de la fila; si ya existe la revisión con ese hash, no duplica. Los scripts aceptan `--case-id`, `--limit`, `--dry-run`, `--resume-after`.

### 7.5 Reindexado pgvector

- `index_structured` borra y crea `fact`/`claim`/`event`/`decision`/`evidence`; no toca documentos/media.
- `index_case` reconstruye todo el caso; usar solo en migración inicial o reparación.
- Batch de embeddings `EMBEDDING_BATCH_SIZE`; reintentos; no bloquear la API.
- Al terminar: `DELETE cache prefix retrieval:{case_id}`, `ANSWER_CACHE_VERSION` bump, `graph_build`.
- Validar: `count(chunks where case_id and chunk_type='fact') > 0` para casos con hechos; `embedding is not null`; muestra de búsqueda.

### 7.6 Grafo

- `graph.build_case_graph` se amplía:
  - `facts` SELECT incluye `proof_status`, `is_proven`, `fact_code`.
  - `fact_positions` -> edges de parte.
  - `evidence_links` -> `SUPPORTS`/`REFUTES`.
  - `decisions` -> `DECISION_PROVES_FACT`.
  - `issues` -> `FACT_ABOUT_ISSUE`.
- `graph.evidence_matrix` incluye `proof_status`, `position`, `decision`.
- `GET /graph/nodes?node_type=Fact&proof_status=...`.
- Rebuild tras cada cambio de hechos; deduplicar con `graph_build` como hoy.

### 7.7 CKP y auditoría

- `CKP_VERSION = "1.1"`.
- Añadir:
  - `entities/facts.jsonl` (ya existe)
  - `entities/fact_positions.jsonl`
  - `entities/fact_revisions.jsonl`
  - `audits/answer_audits.jsonl`
  - `audits/answer_audit_findings.jsonl`
  - `audits/second_opinion_runs.jsonl`
  - `audits/second_opinion_claims.jsonl`
  - `entities/legal_rule_citations.jsonl`
- Manifest: counts de audits, second opinions, proven facts; `layout` actualizado.
- `audit_logs` incluye nuevas acciones (`answer.audited`, `second_opinion.requested`, `fact.proven`, `fact.reviewed`).
- `README.md` del CKP explica que la fuente primaria sigue siendo el PDF/video.
- `purge.purge_case` incluye `answer_audits`, `answer_audit_*`, `second_opinion_*`, `fact_positions`, `fact_revisions` y `legal_rule_citations`; si `legal_hold` está activo, no purga. El borrado es por cascada de `case_id` y se prueba con un caso sintético.

### 7.8 Archify

`docs/architecture/` contiene los seis diagramas del plan:
- `00-system-context.html`
- `01-legal-guard-flow.html`
- `02-second-opinion-sequence.html`
- `03-facts-lifecycle.html`
- `04-data-model.html`
- `05-ux-navigation.html`
- `README.md` con índice, evidencia y comando de regeneración.

Los diagramas se generan con la skill **archify**, en HTML autónomo, con tema
claro/oscuro y SVG inline. Se enlazan desde este plan, `docs/CHAT_PIPELINE_CONTRACT.md`,
`docs/MCP_PARITY.md` y el índice de arquitectura.

---

## 8. Superficie API y permisos

### 8.1 Permisos nuevos

Añadir a `config/rbac.yaml`:

| Permiso | ORG_ADMIN | CASE_MANAGER | LAWYER | REVIEWER | ANALYST | READ_ONLY |
|---|---|---|---|---|---|---|
| `audit.read` | sí | sí | sí | sí | sí | sí |
| `audit.run` | sí | sí | sí | no | no | no |
| `audit.review` | sí | sí | sí | sí | no | no |
| `second_opinion.read` | sí | sí | sí | sí | sí | no |
| `second_opinion.run` | sí | sí | sí | no | no | no |
| `facts.read` | sí | sí | sí | sí | sí | sí |
| `facts.write` | sí | sí | sí | no | no | no |
| `facts.review` | sí | sí | sí | sí | no | no |
| `verification_policy.write` | sí | no | no | no | no | no |

- `case_access` sigue siendo la puerta; ningún endpoint acepta un caso ajeno.
- `ORG_ADMIN` mantiene `*`; las nuevas tablas tienen RLS.
- El worker usa `actor_id` del run y RLS; no eleva privilegios.

### 8.2 Endpoints

#### Auditoría
- `POST /v1/cases/{case_id}/messages/{message_id}/audit`
- `GET /v1/cases/{case_id}/audits?message_id=&status=&limit=`
- `GET /v1/cases/{case_id}/audits/{audit_id}`
- `POST /v1/cases/{case_id}/audits/{audit_id}/review`
- `GET /v1/cases/{case_id}/verification-policy`
- `PUT /v1/cases/{case_id}/verification-policy`

#### Segunda opinión
- `POST /v1/cases/{case_id}/messages/{message_id}/second-opinion`
- `GET /v1/cases/{case_id}/second-opinions`
- `GET /v1/cases/{case_id}/second-opinions/{run_id}`
- `POST /v1/cases/{case_id}/second-opinions/{run_id}/cancel`
- `POST /v1/cases/{case_id}/second-opinions/{run_id}/review`

#### Hechos
- `GET /v1/cases/{case_id}/facts?proof=&side=DEMANDANTE|DEMANDADO&party_id=&issue_id=&q=&limit=`
- `GET /v1/cases/{case_id}/facts/{fact_id}`
- `POST /v1/cases/{case_id}/facts/rebuild`
- `PATCH /v1/cases/{case_id}/facts/{fact_id}`
- `POST /v1/cases/{case_id}/facts/{fact_id}/review`
- `GET /v1/cases/{case_id}/facts/{fact_id}/revisions`
- `GET /v1/cases/{case_id}/facts/export?format=json|csv`

#### Administración
- `GET/PUT /v1/admin/verification-policy` (política global)
- `GET /v1/admin/models?task=second_opinion` (modelos activos distintos)
- `GET /v1/admin/audit-metrics` (conteos, no contenido sensible)

### 8.3 Contratos de respuesta

Todas las respuestas nuevas usan JSON tipado y `Strict` de Pydantic:
- `AuditOut`: `id`, `case_id`, `status`, `mode`, `claims_total`, `claims_supported`, `claims_blocked`, `findings`, `duration_ms`, `model_run_id`, `created_at`.
- `FindingOut`: `code`, `severity`, `message`, `claim_index`, `source`, `blocking`.
- `SecondOpinionOut`: `id`, `status`, `verdict`, `summary`, `responder_model`, `verifier_model`, `differences`, `cost`, `duration_ms`.
- `FactOut`: `id`, `proposition`, `proof_status`, `is_proven`, `proof_basis`, `positions`, `evidence`, `citations`, `decision`, `version`.
- Ningún endpoint devuelve `password`, `api_key` ni prompts internos.

---

## 9. Plan de frontend y responsive

La guía completa está en [GUIA_DISENO_MODULOS_LEGALES.md](GUIA_DISENO_MODULOS_LEGALES.md). Resumen ejecutable:

### 9.1 Rutas

- `/dashboard/procesos/[caseId]` sigue siendo el expediente.
- Se añade un layout de proceso con `ProcessHeader` y `ProcessTabs`:
  - `Expediente` (actual)
  - `Hechos` (`/dashboard/procesos/[caseId]/hechos`)
  - `Auditoría` (`/dashboard/procesos/[caseId]/auditoria`)
  - `Cronología` (abre el diálogo actual)
- `/dashboard/verificacion` para política global y modelos de segunda opinión (admin).
- `ChatWidget` añade badge de auditoría y botón de segunda opinión por mensaje.

### 9.2 Componentes nuevos

| Componente | Función |
|---|---|
| `ProcessHeader` | Título, radicado, estado, acciones; reutiliza el header actual. |
| `ProcessTabs` | Navegación por pestañas, `aria-current`, teclado, scroll horizontal en móvil. |
| `FactsBoard` | Filtros y lista de hechos; estados `Probados`, `Con prueba`, `Discutidos`, `Todos`. |
| `FactCard` | Proposición, `ProofBadge`, posiciones de parte, fuentes. |
| `FactDetailSheet` | Detalle, evidencia, decisión, historial, revisión. |
| `PartyPositionBadge` | `Alega`, `Niega`, `Admite`, `Sin posición`; texto + icono. |
| `AuditBadge` | `Verificado`, `Advertencia`, `Bloqueado`, `Sin auditar`. |
| `AuditPanel` | Resumen, claims, findings, citas, acciones. |
| `AuditFindingRow` | Código, severidad, mensaje, claim, fuente. |
| `SecondOpinionDialog` | Solicitud, estado, comparación lado a lado. |
| `SecondOpinionDiff` | Coincidencias/diferencias por claim. |
| `VerificationPolicyForm` | Política global y por caso; modelos; umbrales. |

### 9.3 Estados obligatorios

- Vacío: sin hechos, sin auditorías, sin segunda opinión.
- Cargando: skeletons con la forma real; no texto “Cargando…” solo.
- Error: mensaje, request_id, reintento.
- Permiso: CTA de configuración, no un error genérico.
- Parcial: badge y límites visibles.
- Móvil: cards, no tablas que se salgan.

### 9.4 Accesibilidad

- WCAG 2.2 AA; contraste 4.5:1; foco visible; teclado; `aria-live` para auditoría; `Escape` en diálogos; etiquetas de tablas; no solo color para estados; `prefers-reduced-motion`.
- Pruebas en 360, 768, 1024, 1440 px.

---

## 10. Contratos de harness

El detalle está en [CONTRATOS_HARNESS_MODULOS_LEGALES.md](CONTRATOS_HARNESS_MODULOS_LEGALES.md). Resumen:

### 10.1 Harness de construcción

| Fase | Agente responsable | Skills obligatorias | Entregable |
|---|---|---|---|
| Arquitectura y datos | `architect` + `database-optimizer` | `architecture-decision-records`, `api-and-interface-design` | ADR, ER, migraciones |
| Backend antialucinación | `backend-architect` + `rag-pipeline-engineer` | `test-driven-development`, `security-and-hardening` | `legal_guard`, tests |
| Segunda opinión | `ai-engineer` + `prompt-engineer` | `source-driven-development`, `verification-loop` | worker, prompts, comparador |
| Hechos | `data-engineer` + `database-optimizer` | `graphify`, `test-driven-development` | extracción, posiciones, proof |
| Frontend | `frontend-developer` + `ui-designer` | `design-taste-frontend`, `frontend-ui-engineering`, `ui-ux-pro-max` | pestañas, paneles |
| Seguridad | `security-architect` + `security-reviewer` | `security-and-hardening` | RLS, prompt injection, secrets |
| QA | `test-automation-engineer` + `e2e-runner` | `e2e-testing`, `browser-qa` | unit, integration, e2e |
| Docs/diagramas | `technical-writer` + `archify` | `living-docs-governance`, `archify` | docs, diagramas |

### 10.2 Agentes de runtime

| Agente | Misión | Tools | Skills | Salida |
|---|---|---|---|---|
| `Verificador legal` | Auditar claims contra fuentes, normas, partes y decisiones | Solo lectura: `search_case`, `read_document`, `get_document_page`, `find_person`, `graph_query`, `get_claims`, `verify_citation`, `lookup_legal_rule` | Cita primaria, cifras/fechas, partes, normas, contradicciones, decisión | JSON de veredictos y findings |
| `Auditor de segunda opinión` | Responder de forma independiente y comparar | Misma allowlist de solo lectura | Respuesta independiente, comparación probatoria, disenso | JSON de respuesta + comparación |
| `Analista de hechos` | Extraer hechos y posiciones de parte | Solo lectura | Posición de parte, prueba admitida, hecho probado, revisión | JSON de hechos candidatos |

Los tres se siembran como agentes de sistema, con prompts versionados y skills enlazadas. No reemplazan al `Agente jurídico (anti-alucinación)` actual; lo complementan.

---

## 11. Fases, tareas y orden de implementación

### Fase 0: preparación y contratos

| ID | Tarea | Archivos | Depende | Criterio |
|---|---|---|---|---|
| P0-01 | Congelar baseline y crear rama/worktree | repo | - | commit base `963297c` |
| P0-02 | Aprobar decisiones D1-D30 y contratos | este plan, contracts | P0-01 | sin decisiones abiertas |
| P0-03 | Crear flags `LEGAL_GUARD_ENABLED`, `SECOND_OPINION_ENABLED`, `FACTS_PROOF_ENABLED` | `config.py`, `.env.example` | P0-02 | flags off por defecto |
| P0-04 | Fixtures sintéticos de dos procesos y golden set inicial | `tests/fixtures`, `evals` | P0-01 | 2 casos aislados |

### Fase 1: base de datos

| ID | Tarea | Archivos | Depende | Criterio |
|---|---|---|---|---|
| P1-01 | Migración 0055 legal guard | `0055_legal_guard.py`, `055_legal_guard.sql` | P0-03 | upgrade/downgrade |
| P1-02 | Migración 0056 segunda opinión | `0056_second_opinion.py`, `056_second_opinion.sql` | P1-01 | FK/índices |
| P1-03 | Migración 0057 hechos | `0057_party_facts.py`, `057_party_facts.sql` | P1-02 | checks/generated |
| P1-04 | Migración 0058 índices/RLS | `0058_facts_graph_indexes.py`, `058_facts_graph_indexes.sql` | P1-03 | policies/grants |
| P1-05 | Backfill idempotente | `scripts/backfill_legal_modules.py` | P1-04 | dry-run + 2 casos |
| P1-06 | Importador de normas | `scripts/import_legal_rules.py`, `data/legal_rules/co/*.json`, `checksums.json` | P1-04 | checksum/version; pack piloto Código Civil, CGP/Ley 1564 y normas citadas en los casos |
| P1-07 | Reindexado y grafo | `scripts/reindex_facts.py`, `scripts/rebuild_graphs.py` | P1-05 | chunks/graph |
| P1-08 | Rehearsal en BD de prueba | docker/test DB | P1-01..07 | up/down/backfill |

### Fase 2: antialucinación

| ID | Tarea | Archivos | Depende | Criterio |
|---|---|---|---|---|
| P2-01 | Esquemas y prompts v4/v3 | `schemas.py`, `packages/prompts/answer_question.v4.md`, `verify_answer.v3.md` | P1 | JSON schema |
| P2-02 | `legal_guard` service | `services/legal_guard.py` | P2-01 | unit |
| P2-03 | Validadores deterministas | `services/legal_rules.py`, `services/claim_grounding.py` | P2-02 | cifras/fechas/partes/normas |
| P2-04 | Agente verificador + skills | `builtin_agents.py`, `ensure_legal_agents`, seeds | P2-02 | allowlist; seeding idempotente por nombre, aunque la org ya tenga agentes |
| P2-05 | Integración query/chats/SSE | `routers/query.py`, `routers/chats.py` | P2-04 | audit_id/status |
| P2-06 | API de auditoría | `routers/audits.py` | P2-05 | permisos |
| P2-07 | UI de auditoría | `components/audit-*`, `app-sidebar` | P2-06 | estados |
| P2-08 | Tests y golden set | `tests/unit`, `tests/integration` | P2-07 | 0 critical |

### Fase 3: segunda opinión

| ID | Tarea | Archivos | Depende | Criterio |
|---|---|---|---|---|
| P3-00 | Sembrar/validar modelo de segunda opinión | `seed_ai_models.py`, `data/modelos/segunda_opinion.json` | P1 | modelo activo y distinto |
| P3-01 | Configuración por caso | `schemas.py`, `routers/audits.py`, admin UI | P3-00 | modelo distinto |
| P3-02 | Prompt independiente + comparador | `packages/prompts/second_opinion_*.md` | P3-01 | JSON schema |
| P3-03 | Worker `second_opinion` | `workers/handlers/second_opinion.py`, dispatcher | P3-02 | idempotente |
| P3-04 | API runs | `routers/second_opinion.py` | P3-03 | estados |
| P3-05 | UI comparación | `components/second-opinion-*` | P3-04 | responsive |
| P3-06 | Tests de independencia/costo | `tests/unit`, `tests/integration` | P3-05 | no ve respuesta |

### Fase 4: hechos

| ID | Tarea | Archivos | Depende | Criterio |
|---|---|---|---|---|
| P4-01 | Prompt/schema de extracción | `party_facts.v1.md`, `fact.schema.json` | P1 | citas |
| P4-02 | Servicio de posiciones/proof | `services/party_facts.py` | P4-01 | taxonomía |
| P4-03 | Endpoints de hechos | `routers/facts.py`; mover `GET /cases/{id}/facts` desde `cases.py` | P4-02 | sin ruta duplicada; permisos |
| P4-04 | UI tablero/detalle | `components/facts-*`, `[caseId]/hechos` | P4-03 | filtros |
| P4-05 | Grafo/chunks | `graph.py`, `chunking.py`, `indexing.py` | P4-02 | rebuild |
| P4-06 | Revisiones/reviews | `fact_revisions`, `review.py` | P4-03 | append-only |
| P4-07 | Tests dos procesos | `tests/integration` | P4-06 | aislamiento |

### Fase 5: cross-cutting

| ID | Tarea | Archivos | Depende | Criterio |
|---|---|---|---|---|
| P5-01 | CKP 1.1 + purge/legal hold | `services/export.py`, `services/purge.py` | P2/P3/P4 | export completo; purga incluye tablas nuevas; legal hold bloquea |
| P5-02 | Archify | `docs/architecture/*` | P2/P3/P4 | 6 diagramas |
| P5-03 | Contratos/docs | `docs/CHAT_PIPELINE_CONTRACT.md`, `MCP_PARITY.md` | P5-01 | docs actualizadas |
| P5-04 | Métricas/observabilidad | `services/metrics.py`, dashboards | P2/P3/P4 | métricas |
| P5-05 | Migración de datos real | scripts | P1..P4 | dry-run + 2 casos |

### Fase 6: calidad

| ID | Tarea | Criterio |
|---|---|---|
| P6-01 | Unitarias nuevas | 100% de servicios críticos |
| P6-02 | Integración API + DB | RLS, dos procesos, migraciones |
| P6-03 | E2E Playwright | flujo completo hechos/auditoría/segunda opinión |
| P6-04 | Golden set | umbrales de 4.3/5.8/6.8 |
| P6-05 | Seguridad | RLS, prompt injection, tools, secrets |
| P6-06 | Performance | guard p95, second opinion p95, reindex |
| P6-07 | Migración/rollback | up/down/backfill |
| P6-08 | Aceptación | checklist de sección 14 |

### Fase 7: despliegue

| ID | Tarea | Criterio |
|---|---|---|
| P7-01 | Local | migraciones + seeds + reindex + pruebas |
| P7-02 | Develop | flag off, migrar, backfill, verificar |
| P7-03 | Quality | misma secuencia, datos de prueba |
| P7-04 | Producción | ventana, backup, migrar, backfill, reindex, flag on |
| P7-05 | Monitoreo | errores, latencia, costo, findings |
| P7-06 | Cierre | aceptación firmada y CKP |

---

## 12. Pruebas y evaluación

### 12.1 Unitarias

- `test_legal_guard.py`: reglas, severidades, decisión, idempotencia.
- `test_claim_grounding.py`: números, fechas, montos, porcentajes, nombres, alias.
- `test_legal_rules.py`: jurisdicción, versión, norma inexistente, norma obsoleta.
- `test_second_opinion.py`: independencia, modelo distinto, comparación, cancelación.
- `test_party_facts.py`: posiciones, taxonomía, `JUDICIALLY_PROVEN` sin decisión, `ADMITTED`.
- `test_fact_revisions.py`: versionado, append-only, override.
- `test_export_legal_modules.py`: CKP 1.1.

### 12.2 Integración

- `test_audits_api.py`: POST/GET/review, RLS, dos casos.
- `test_second_opinion_worker.py`: job, modelo, error, idempotencia.
- `test_facts_api.py`: filtros, PATCH, revisión, versión.
- `test_migrations_legal_modules.py`: upgrade/downgrade, constraints.
- `test_case_isolation_legal.py`: dos procesos, sin mezcla.

### 12.3 E2E

- Login, abrir proceso, pestaña Hechos, ver probados, abrir detalle, revisar.
- Chat, respuesta, badge de auditoría, abrir panel, ver finding.
- Solicitar segunda opinión, ver estado, comparación, revisar.
- Responsive 360/768/1024/1440; teclado; tema claro/oscuro.
- Fuente rota: error state, reintento.

### 12.4 Golden set y umbrales

| Métrica | Umbral |
|---|---|
| Citas primarias válidas en claims publicados | 100% |
| Claims con cifra/fecha/parte/norma no soportada | 0 |
| `JUDICIALLY_PROVEN` sin decisión | 0 |
| Contradicciones publicadas con una sola versión | 0 |
| Cobertura de auditoría de respuestas | 100% |
| Precisión de hechos “probados” | 100% en golden set |
| Recall de hechos probados | >= 90% en golden set |
| Segunda opinión completada | >= 95% sin error |
| p95 guard determinista | < 300 ms |
| p95 segunda opinión | < 180 s |
| p95 chat normal | sin regresión > 20% |
| Tests unitarios/integración | 0 fallos |
| Seguridad | 0 alta/crítica |

### 12.5 Pruebas de migración y rollback

- Base vacía: `alembic upgrade head`; `downgrade 0054`; volver a subir.
- Base con datos: copia de dos casos; backfill dry-run; backfill real; reindex; graph; comparar conteos.
- Reindexado: chunks `fact` > 0; embeddings no nulos; búsqueda devuelve hechos.
- Rollback: flags off; columnas nuevas ignoradas; no se pierde nada; auditorías históricas legibles.

---

## 13. Despliegue, flags y rollback

### 13.1 Orden

```text
1. Backup de BD y storage.
2. Alembic `0055` a `0061` (o `head` cuando `0062` esté validada).
3. Deploy API + workers + web con flags OFF.
4. Backfill legal modules (dry-run, lote, real).
5. Reindex facts + graph por caso.
6. Verificación de conteos y smoke tests.
7. Activar LEGAL_GUARD_ENABLED en modo BALANCED.
8. Activar SECOND_OPINION_ENABLED por caso piloto.
9. Activar FACTS_PROOF_ENABLED en proceso piloto.
10. Monitorear y ampliar.
```

### 13.2 Flags

- `LEGAL_GUARD_ENABLED=false` por defecto en `config.py` (P0-03).
- `LEGAL_GUARD_DEFAULT_MODE=BALANCED`; en código el campo es `LEGAL_GUARD_MODE`.
- `SECOND_OPINION_ENABLED=false` por defecto.
- `FACTS_PROOF_ENABLED=false` por defecto. Ojo: al 2026-10-22 ningún servicio lee
  este flag; es un no-op hasta que P1 lo conecte o lo elimine.
- `LEGAL_NORM_STRICT=true`.
- `AUDIT_RETENTION_DAYS` y `SECOND_OPINION_MAX_COST` por org.

Activar solo después de migrar y verificar, por entorno y de forma explícita:

```dotenv
LEGAL_GUARD_ENABLED=true
LEGAL_GUARD_MODE=BALANCED
SECOND_OPINION_ENABLED=true
FACTS_PROOF_ENABLED=true
```

Local y `.env.test` ya llevan estas líneas para no perder la cobertura; los
entornos develop/quality/production deben añadirlas al desplegar, porque al no
estar definidas heredan el `false` del código.

### 13.3 Rollback

- Flags off primero.
- Si hay error de datos, `alembic downgrade 0054` solo después de backup; las tablas nuevas no afectan al código viejo.
- Si el backfill falla, se reanuda por `resume-after`; no se borra nada.
- Si el reindexado falla, se conserva el índice anterior; se reconstruye por documento/caso.

---

## 14. Criterios de aceptación globales

- [ ] Los tres módulos están detrás de flags y se activan sin redeploy.
- [ ] `cases` sigue siendo el proceso/demanda; dos procesos no se mezclan.
- [ ] Toda respuesta nueva publicada tiene auditoría persistida; las históricas se auditan bajo demanda.
- [ ] Toda afirmación publicada tiene cita primaria; cifras, fechas, partes y normas coinciden.
- [ ] La segunda opinión es independiente, con otro modelo, asíncrona e idempotente.
- [ ] Los hechos “probados” solo son `ADMITTED` o `JUDICIALLY_PROVEN`.
- [ ] Las posiciones de demandante y demandado son visibles y trazables.
- [ ] Grafo, chunks y CKP reflejan hechos/auditorías.
- [ ] Migraciones up/down y backfill idempotente probados.
- [ ] RLS, grants y triggers de misma organización en todas las tablas.
- [ ] UI responsive, accesible, clara y sin estados falsos.
- [ ] Documentación, Archify y contratos actualizados.
- [ ] 0 hallazgos críticos del golden set y 0 regresiones en los cuatro casos.
- [ ] No hay decisiones abiertas ni tareas sin dueño.

---

## 15. Riesgos y cómo quedan cerrados

| Riesgo | Mitigación cerrada |
|---|---|
| El modelo inventa una norma | `legal_rules` obligatorio; si no está, se bloquea. |
| El modelo inventa una cifra o fecha | Validación exacta contra la cita; claim eliminado. |
| El modelo confunde partes | Validación contra `parties` y `fact_positions`. |
| El juez semántico es demasiado permisivo | Determinista primero; juez complementario; golden set. |
| La segunda opinión usa el mismo modelo | Validación de `model_id` distinto; sin fallback. |
| La segunda opinión bloquea el chat | Worker asíncrono; timeout; estado. |
| El costo se dispara | `max_llm_tokens`, `SECOND_OPINION_MAX_COST`, idempotencia, solo bajo demanda. |
| El backfill bloquea la BD | Scripts por lotes, `--dry-run`, `--resume-after`; DDL aditivo. |
| El reindexado rompe pgvector | No cambia dimensión; reindex por caso; validación de conteos. |
| El grafo queda inconsistente | `graph_build` después de cambios; tests de conteo. |
| La UI se vuelve inmanejable | Pestañas, no módulos globales; guía de diseño; e2e responsive. |
| Un humano aprueba sin control | `reviews` + `fact_revisions`; override con motivo; audit log. |
| Fuga entre procesos | `case_id` en todo; RLS; trigger; tests de dos casos. |
| OCR/ASR de baja calidad | Umbral de confianza; `NEEDS_REVIEW`; no se usa para probar. |
| No hay modelo de segunda opinión | Feature deshabilitada con CTA; no se simula. |
| No hay normas oficiales cargadas | Modo estricto bloquea conclusiones; importador y seed. |
| La migración falla en producción | Backup, ensayo, downgrade, flags off. |

---

## 16. Definición de terminado

Un punto se considera cerrado solo cuando:

1. El código está en `main` con tests verdes.
2. La migración está aplicada y probada en local, develop y quality.
3. El backfill/reindexado está ejecutado y validado con dos procesos.
4. La UI está verificada en escritorio y móvil, con teclado y tema claro/oscuro.
5. El golden set cumple los umbrales.
6. La seguridad no tiene hallazgos altos/críticos.
7. La documentación, contratos, Archify y CKP están actualizados.
8. El rollback está probado.
9. El responsable legal/humano firma la aceptación.
10. No quedan tareas, decisiones ni bloqueantes en la tabla de control.

---

## 17. Inventario de archivos

### Nuevos

- `apps/api/app/services/legal_guard.py`
- `apps/api/app/services/legal_rules.py`
- `apps/api/app/services/claim_grounding.py`
- `apps/api/app/services/party_facts.py`
- `apps/api/app/services/second_opinion.py`
- `apps/api/app/routers/audits.py`
- `apps/api/app/routers/facts.py`
- `apps/api/app/routers/second_opinion.py`
- `apps/api/app/workers/handlers/second_opinion.py`
- `apps/api/app/workers/handlers/facts_build.py`
- `apps/api/migrations/versions/0055_legal_guard.py` a `0061_second_opinion_worker.py` (y `0062_legal_gate_semantics.py`, P1)
- `apps/api/db/sql/055_legal_guard.sql` a `058_facts_graph_indexes.sql`
- `packages/prompts/answer_question.v4.md`
- `packages/prompts/verify_answer.v3.md`
- `packages/prompts/legal_guard_system.v1.md`
- `packages/prompts/second_opinion_answer.v1.md`
- `packages/prompts/second_opinion_compare.v1.md`
- `packages/prompts/party_facts_extract.v1.md`
- `packages/prompts/fact_proof_review.v1.md`
- `packages/schemas/legal_audit.schema.json`
- `packages/schemas/second_opinion.schema.json`
- `packages/schemas/party_fact.schema.json`
- `apps/web/src/app/dashboard/procesos/[caseId]/layout.tsx`
- `apps/web/src/app/dashboard/procesos/[caseId]/hechos/page.tsx`
- `apps/web/src/app/dashboard/procesos/[caseId]/auditoria/page.tsx`
- `apps/web/src/app/dashboard/verificacion/page.tsx`
- `apps/web/src/components/process-tabs.tsx`
- `apps/web/src/components/facts-board.tsx`
- `apps/web/src/components/fact-card.tsx`
- `apps/web/src/components/fact-detail-sheet.tsx`
- `apps/web/src/components/audit-badge.tsx`
- `apps/web/src/components/audit-panel.tsx`
- `apps/web/src/components/second-opinion-dialog.tsx`
- `apps/web/src/components/second-opinion-diff.tsx`
- `apps/web/src/components/verification-policy-form.tsx`
- `scripts/backfill_legal_modules.py`
- `scripts/reindex_facts.py`
- `scripts/rebuild_graphs.py`
- `scripts/import_legal_rules.py`
- `data/legal_rules/co/*.json` + `checksums.json` (el agregado `checksums.json` ya existe y está validado)
- `data/modelos/segunda_opinion.json`
- `docs/architecture/*` (Archify)
- tests nuevos listados en la sección 12.

**Estado real de prompts y schemas al 2026-10-22:**

- `answer_question.v4.md` y `verify_answer.v3.md` existen como **contratos de
  versión** (`loaded_as_prompt: false`): documentan el delta y la condición para
  activarlo. El runtime sigue cargando `answer_question.v3.md`/`v2.md` y
  `verify_answer.v2.md`, así que no hay dos prompts activos ni comportamiento
  duplicado.
- `fact_proof_review.v1.md` es un prompt nuevo y activo como contrato, pero
  `services/party_facts.py` todavía no lo invoca; la recomendación siempre pasa
  por revisión humana y nunca escribe `ADMITTED`/`JUDICIALLY_PROVEN`.
- `legal_audit.schema.json`, `second_opinion.schema.json` y
  `party_fact.schema.json` validan contra metaschema 2020-12 y contra los
  invariantes reales (PASSED sin críticos, COMPLETED con veredicto,
  `is_proven` derivado, `ADMITTED` con posición `ADMITS`).

### Modificados

- `apps/api/app/main.py`
- `apps/api/app/schemas.py`
- `apps/api/app/routers/query.py`
- `apps/api/app/routers/chats.py`
- `apps/api/app/routers/cases.py`
- `apps/api/app/routers/review.py`
- `apps/api/app/routers/graph.py`
- `apps/api/app/services/agent.py`
- `apps/api/app/services/answering.py`
- `apps/api/app/services/compose.py`
- `apps/api/app/services/verification.py`
- `apps/api/app/services/chunking.py`
- `apps/api/app/services/indexing.py`
- `apps/api/app/services/graph.py`
- `apps/api/app/services/export.py`
- `apps/api/app/services/builtin_agents.py`
- `apps/api/app/workers/dispatcher.py`
- `apps/web/src/components/app-sidebar.tsx`
- `apps/web/src/components/chat-widget.tsx`
- `apps/web/src/app/dashboard/procesos/[caseId]/page.tsx`
- `config/rbac.yaml`
- `docs/CHAT_PIPELINE_CONTRACT.md`
- `docs/MCP_PARITY.md`
- `docs/SSD_Judicial_AI_Platform.md` (addendum)
- `README.md` / `docs/DEPLOY_GCP.md` (operación)
- `Makefile` (targets `audit`, `facts`, `reindex-facts`)

---

## 18. Control de cierre

| Control | Evidencia | Responsable |
|---|---|---|
| Plan aprobado | este documento | arquitecto |
| Contratos aprobados | `CONTRATOS_HARNESS_MODULOS_LEGALES.md` | arquitecto + legal |
| Diseño aprobado | `GUIA_DISENO_MODULOS_LEGALES.md` | producto + UI |
| Migraciones | `alembic upgrade/downgrade` | backend/DB |
| Backfill | reporte dry-run + real | data |
| Reindex | conteos chunks/embeddings | backend |
| Grafo | nodos/aristas por caso | backend |
| Pruebas | unit/integration/e2e | QA |
| Golden set | reporte de métricas | QA + legal |
| Seguridad | reporte sin críticos | security |
| Rendimiento | p50/p95 | performance |
| Docs/Archify | índice y 6 diagramas validados (`architecture/README.md`) | docs |
| Aceptación | firma | responsable |

**Estado del plan (2026-10-22):** ejecutado en local sobre `0055` a `0061`; la
`0062` está en curso por P1. Verificado: gate legal, segunda opinión (corrida
real) y ledger de hechos; 6/6 diagramas Archify; prompts y schemas del plan
creados. Pendiente: corpus completo y firma legal (P0), golden set y E2E,
corrección de `0062` y despliegue con backup, backfill y reindexado. El detalle,
con evidencia y bloqueos, está en
[`AUDITORIA_MODULOS_LEGALES_2026-10-22.md`](AUDITORIA_MODULOS_LEGALES_2026-10-22.md)
y en [`docs/coordination/LEGAL_MODULES/P2.md`](coordination/LEGAL_MODULES/P2.md).
