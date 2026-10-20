# Chat IA: cómo funciona y cómo refinarlo para que se sienta nativo

> **Actualización 2026-10-19:** este documento describe el estado del commit
> `8618334`. La implementación posterior (router, compositor, SSE, evaluación y
> migraciones) está en `docs/IMPLEMENTACION_CHAT_3_PUNTOS.md`. Los diagramas
> Archify de este directorio también corresponden al estado anterior.

**Auditoría técnica del repositorio `judicial-ai`**  
Commit auditado: `8618334` · Fecha: 2026-10-19  
Alcance: pipeline de chat, base de conocimiento, agentes/skills/modelos, MCP y experiencia de conversación.  
Método: lectura de código con referencias `archivo:línea` + tres auditorías especializadas en paralelo (RAG, arquitectura de IA, UX conversacional).


> **Diagramas actualizados (2026-10-20):** [Arquitectura actual](../.archify/architecture-chat-ia-20261019-141921/chat-ia-arquitectura-actual.html) · [Secuencia actual](../.archify/architecture-chat-ia-20261019-141921/chat-ia-turno-actual.html). Los dos HTML anteriores quedan como histórico del commit 8618334.

Diagramas:

- [Arquitectura: base de conocimiento y pipeline de respuesta](../.archify/architecture-chat-ia-20261019-141921/chat-ia-arquitectura.html)
- [Secuencia: un turno del Chat IA, paso a paso](../.archify/architecture-chat-ia-20261019-141921/chat-ia-turno.html)

---

## 0. Veredicto en diez líneas

1. **La base de conocimiento no es el problema principal.** Hay OCR/ASR, chunks, pgvector, FTS, grafo, citas verificables y RLS. La arquitectura *evidence-first* está bien pensada.
2. **El problema es la capa conversacional.** La respuesta final es literalmente `" ".join(claims)` (`query.py:230`): pierde la prosa del modelo y suena telegráfica.
3. **No hay streaming ni progreso real.** El turno puede encadenar hasta 7 llamadas al LLM antes del primer byte y la UI solo muestra un spinner fijo.
4. **La recuperación es estrecha.** `RETRIEVAL_TOP_K` candidates por rama, RRF casi plano, sin re-ranking, sin reescritura de consulta con el historial y sin deduplicación entre versiones OCR.
5. **El grounding es demasiado estricto en un punto y demasiado laxo en otro.** Descarta claims correctas por falta de `start_mmss` o por sinónimos, pero acepta un fragmento literal de ASR como “respuesta” si tiene cita.
6. **La captura de “pagos” no es un bug de datos.** `answer` y `occurrences` son dos canales independientes: uno puede decir “no hay evidencia” y el otro listar 6 ubicaciones.
7. **Los controles de la UI mienten en parte.** Cambiar agente/modelo después de crear la sesión no afecta al siguiente mensaje (`chats.py:255-258`).
8. **Hay un riesgo de seguridad real.** Las tools de escritura son alcanzables con permiso de lectura `ai.query`, y el loop no escapa los resultados de tools (`agent.py:60-64,171-185`).
9. **La evaluación no mide el pipeline de producción.** `scripts/run_evals.py` existe, pero evalúa `legacy_retrieve(k=500)`, no `retrieve()` ni el agente.
10. **Con 3 olas de cambios se puede pasar de “buscador con citas” a “chat jurídico con evidencia”.** La primera ola es de días y elimina las inconsistencias más visibles.

---

## 1. La idea en una frase

> El chat no “sabe” del expediente: **recupera fragmentos citables** y solo deja decir al modelo aquello que puede anclarse a un `handle` de evidencia (`E1`, `E2`, …). Si no hay anclaje, la afirmación se descarta.

Eso explica tanto su virtud (no inventa) como su defecto (a veces no dice nada útil aunque haya material).

---

## 2. Recorrido de un turno

Vista resumida; el detalle visual está en el [diagrama de secuencia](../.archify/architecture-chat-ia-20261019-141921/chat-ia-turno.html).

| # | Etapa | Quién | Evidencia |
|---|---|---|---|
| 1 | El usuario escribe y la UI pinta el mensaje de forma optimista | `chat-widget.tsx` | `send()` `:255-260`, `sendText()` `:262-290` |
| 2 | Si no hay sesión, se crea con `agent_id`, `model_id`, `ocr_mode`, `asr_mode` | `POST /cases/{id}/chats` | `chat-widget.tsx:272-283`, `chats.py:155-178` |
| 3 | Se envía el mensaje con `strategy: "agent"` | `POST /cases/{id}/chats/{sid}/messages` | `chat-widget.tsx:284-290` |
| 4 | Guardas: permiso `ai.query`, rate limit, presupuesto de tokens | `send_message` | `chats.py:198-203` |
| 5 | Se cargan los últimos K turnos y se guarda el mensaje del usuario | `chats.py` | `:206-214` |
| 6 | Atajos deterministas: confirmar/cancelar corrección pendiente | `correction` + `chat_pending_corrections` | `chats.py:227-243` |
| 7 | Se arma `QueryIn` con el agente/modelo **de la sesión** | `chats.py` | `:255-258` |
| 8 | `_run_query` elige el proveedor del modelo o el global | `query.py` | `:83-85`, `llm.py:126-152` |
| 9 | El agente ejecuta hasta 5 pasos de tool-calling | `agent.py` | `:18`, `:157-185` |
| 10 | Síntesis con `answer_question.v1.md` y validación de citas | `agent.py` / `answering.py` | `agent.py:202-205`, `answering.py:272-298` |
| 11 | Juez semántico LLM sobre los claims citados | `verification.py` | `:36-54` |
| 12 | Se persiste `model_run`, citas y presupuesto | `query.py` | `:166-177` |
| 13 | Se calculan `occurrences` con `locate()` **aparte** | `chats.py` | `:270-283` |
| 14 | Se guarda el mensaje assistant y se devuelve todo | `chats.py` | `:293-307` |
| 15 | La UI recarga el historial y pinta Markdown, citas, ubicaciones y tarjetas | `chat-widget.tsx` | `:291-303`, `:461-534` |

### El coste oculto de un turno

En el peor caso:

- 5 llamadas al LLM por el loop ReAct (`agent.py:157`),
- 1 llamada de síntesis (`agent.py:204`),
- 1 llamada al juez semántico (`verification.py:44`),
- 1 consulta `locate(k=300)` para las ubicaciones (`chats.py:276-278`).

**Siete llamadas secuenciales antes de mostrar la primera palabra.** De ahí la sensación de “se quedó pensando”.

---

## 3. Agentes, skills y modelos: qué es cada cosa

La confusión más común es creer que un “agente” cambia el comportamiento del sistema. Hoy, en este código, **un agente y una skill son texto; el modelo es ejecución**.

### 3.1 Tabla de verdad

| Pieza | Dónde vive | Cómo se crea | Efecto real en ejecución |
|---|---|---|---|
| **Modelo** | Tabla `ai_models` (`provider`, `model_name`, `api_key_ref`, `encrypted_api_key`, `api_base_url`, `is_default`) | `/admin/models` (`admin.py:324-446`) | **Ejecución real.** `get_llm_for_model()` construye el adaptador (`llm.py:126-152`). Si el id no existe o es de otra org, cae al modelo global **sin avisar** (`:136-138`). |
| **Agente** | Tabla `agents`: `system_prompt`, `skills text[]`, `kind`, `is_system` (`011_admin_tables.sql`) | `/admin/agents` (`admin.py:211-269`) | **Solo texto.** Su prompt se antepone al contrato del agente (`agent.py:29-46`). No cambia tools, modelo, memoria ni estado. |
| **Skill** | Tabla `skills`: `name`, `system_prompt` | `/admin/skills` (`admin.py:271-322`) | **Solo texto pegado** como `[Skill: nombre]` (`agent.py:44`). Sin tools propias, sin parámetros, sin validación. |
| **MCP** | `apps/mcp_server` | Servidor aparte | **Ejecución real**, pero **no** usa agentes, skills ni el loop ReAct (`server.py:62-105`). |
| **Prompt** | `packages/prompts/*.vN.md` | Se crea `vN+1`; no se edita el anterior | Define contrato de tools y formato (`agent_system.v7.md`) o contrato de respuesta (`answer_question.v1.md`). |

### 3.2 Cómo se compone el prompt de un turno

```
[system_prompt del agente]
[Skill: Resumen del expediente] ...
[Skill: Detección de contradicciones] ...
[contrato agent_system.v7.md: tools, formato JSON, reglas]
```

- `load_agent_prompt()` lee el agente y sus skills (`agent.py:29-46`).
- Se antepone al contrato base (`agent.py:21-26`).
- El historial, la pregunta y los adjuntos se escapan con `html.escape` (`agent.py:137-151`).

**Detalle importante:** las skills se leen con `WHERE id IN (...)` **sin `ORDER BY`** (`agent.py:43`). El comentario promete “en el orden en que fueron enlazadas”, pero PostgreSQL no lo garantiza: el prompt puede cambiar entre ejecuciones.

### 3.3 Agentes y skills que ya existen

`builtin_agents.py` siembra, por organización:

- **Agente jurídico (anti-alucinación)** con 6 skills: resumen, contradicciones, cronología, análisis de pruebas, obligaciones y redacción de borradores.
- **Agente OCR** y **Agente ASR**, cada uno con su skill de procesamiento.
- Solo se siembran si la organización no tiene agentes; después el usuario puede editarlos o borrarlos.

### 3.4 Lo que un agente **no** puede hacer hoy

- Elegir su propio modelo.
- Restringir tools (las 30 están siempre disponibles).
- Ejecutar skills como pasos.
- Tener memoria propia.
- Definir formato de respuesta distinto.

Eso no es un defecto de implementación aislado: es el diseño actual. La palanca está en convertirlos en configuración ejecutable (ver §8).

---

## 4. La base de conocimiento: qué hay realmente

### 4.1 No hay una “base vectorial aparte”

Todo vive en **PostgreSQL**:

| Almacén | Contenido | Rol |
|---|---|---|
| `document_pages` | Texto OCR por página, folio, `tsv` | Evidencia primaria de documentos |
| `transcript_segments` | Segmentos ASR con `start_ms`/`end_ms`, hablante, `tsv` | Evidencia primaria de audio/video |
| `chunks` | Texto, metadata, `embedding vector(EMBEDDING_DIMENSIONS)`, `tsv` generado | Unidad de recuperación híbrida |
| `graph_nodes` / `graph_edges` | Nodos y aristas del caso, con `provenance` (`EXTRACTED`, `INFERRED`, `AMBIGUOUS`) | Relaciones explícitas, no recuperación automática |
| `agents` / `skills` / `ai_models` | Configuración de IA | Panel de administración |
| `chat_sessions` / `chat_messages` | Conversación, citas, ocurrencias, `model_run_id` | Memoria y trazabilidad |
| `model_runs` | Proveedor, modelo, prompt, versión, tokens, validación | Auditoría de la llamada |
| `citations` | Documento+página o media+segmento+timestamp | Cita verificable |
| `reviews` / `audit_logs` | Correcciones humanas y eventos | Gobernanza |

Los archivos originales (PDF, audio, video) van a almacenamiento local/S3 direccionado por `sha256`.

### 4.2 Qué son los `.md`

Hay dos tipos y conviene no confundirlos:

1. **Prompts versionados** (`packages/prompts/*.vN.md`): `agent_system.v7.md`, `answer_question.v1.md`, `verify_answer.v1.md`, etc. **No son conocimiento indexado**; son instrucciones.
2. **Markdown reconstruido** desde OCR: la tool `get_document_markdown` devuelve una representación Markdown de páginas, pero no se ingesta como fuente independiente.

**No existe** un pipeline que indexe `.md` sueltos como base de conocimiento. El conocimiento del expediente entra por OCR/ASR → páginas/segmentos → chunks.

### 4.3 Chunking

`chunking.py`:

- Documentos: fragmentos de **180–500 palabras**, alineados a secciones/encabezados, sin cortar a mitad de frase (`:26-29`, `:96-121`).
- Testimonios: agrupa segmentos contiguos del mismo hablante con pausas de hasta **30 s** (`_TESTIMONY_GAP_MS`, `:21`).
- Metadata rica: `folio`, `ocr_mode`, `asr_mode`, `entities`, `part/parts`.
- **Sin solape** entre chunks y **sin cruce de página**.

### 4.4 Embeddings

- `indexing.py` borra y recrea chunks por documento, media o caso (`:72-97`).
- Escribe `embedding_model` y `embedding_version` (`:46-52`).
- **La recuperación no filtra por esos campos**: solo `embedding IS NOT NULL` (`answering.py:83`).
- Consecuencia: si cambias de modelo de embeddings, vectores de espacios distintos compiten en la misma búsqueda.

### 4.5 Recuperación híbrida

`answering.retrieve()` (`:22-94`):

1. **Abstención léxica previa** (`abstention.should_abstain`, `:33`): si la cobertura de términos es baja, devuelve `[]` **sin buscar**.
2. Si el caso no tiene ni un chunk, cae a `legacy_retrieve()` sobre páginas/segmentos (`:55-57`).
3. **FTS**: `to_tsvector(FTS_CONFIG, pregunta)`, descarta lexemas < 3 caracteres y los une con `|` (OR), `ts_rank_cd`, `LIMIT k` (`:59-72`). Si `FTS_CONFIG` es `simple`, no hay stemming ni `unaccent`; “apelación” no casa con “apelaciones”.
4. **Vector**: similitud coseno `1 - (embedding <=> query)`, `LIMIT k` (`:75-86`).
5. **Fusión RRF** (`:90`, `:161-170`): `1/(60 + rank)` por lista, recorte final a `k`.
6. Normalización a `document_page` o `transcript_segment` (`:173-189`).

Con `k` pequeño (configurado en `RETRIEVAL_TOP_K`), el RRF es casi plano: el primero y el octavo puntúan muy parecido. No hay margen para que un re-ranker corrija.

### 4.6 Grafo

- `graph.build_case_graph()` reconstruye el grafo del caso desde las tablas estructuradas (`graph.py:84`).
- Nodos: `Case`, `Person`, `Organization`, `Document`, `Claim`, `Fact`, `Evidence`, `Event`, `LegalRule`, `Decision`, `Issue`, `Speaker`.
- Aristas: `ASSERTS`, `SUPPORTS`, `CONTRADICTS`, `REFUTES`, `CITES`, `PARTICIPATED_IN`, `TESTIFIED_IN`, `DECIDES`, `APPLIES`, `DERIVED_FROM`, `MENTIONS`, `ABOUT`, `HAS_SPEAKER`, `IS_PARTY`, `PRECEDES`, `CAUSES`, `APPEALS`, `RESPONDS_TO`, `REFERS_TO`.
- Se consulta **solo si el LLM elige** `graph_query`, `graph_neighbors` o `find_person` (`case_tools/read.py:1218-1318`).
- **No participa en `retrieve()`**: no hay expansión de consulta por entidades ni alias.

### 4.7 MCP

`apps/mcp_server/server.py`:

- Expone las mismas `case_tools` como tools MCP (`:76-105`).
- Recursos `case://{case_id}/files`, `case://{case_id}/graph/stats`, `case://{case_id}/doc/{document_id}/page/{page_number}` (`:108-149`).
- Cada llamada: JWT → `Principal` → `case_access` → `tx(org, actor)` con RLS → post-commit (`:62-70`).
- **No usa agentes ni skills.** Es una superficie para Claude Code, Cursor u otros clientes, no el motor del chat.

---

## 5. Cómo decide y cómo responde

### 5.1 El loop del agente

`agent_system.v7.md` impone:

- Un `tool_call` por turno.
- Respuesta JSON con `thought`, `tool`, `arguments` o `done`.
- Máximo 5 pasos (`MAX_STEPS`, `agent.py:18`).

El loop (`agent.py:157-185`):

1. Llama al LLM con `historial + "What do you do next?"`.
2. Parsea JSON; si falla, **rompe el loop** (`:160-163`).
3. Ejecuta **una** tool.
4. Añade `ACTION:` y `RESULT:` al historial.
5. Repite hasta `done` o 5 pasos.

`thought` se parsea pero **se descarta** (`:160-167`): no se usa para planificar ni se audita.

### 5.2 El contrato de respuesta

`answer_question.v1.md` + `parse_and_validate()` (`answering.py:272-298`):

- El modelo devuelve `claims[]`, cada uno con `text` y `citations[]` (handles `E1…Ek`).
- Un claim sin cita válida → `unsupported_claims`, **nunca** a la respuesta (`:286-289`).
- Se exige solapamiento léxico con la evidencia citada (`grounding`, `:243-255`) y que **todos los números** aparezcan (`:252`).
- Si el solapamiento es menor que `ANSWER_MIN_GROUNDING_OVERLAP` o falta una cifra → `unsupported` (`:291-295`).

Después:

- El juez semántico (`verification.py:36-54`) clasifica los claims citados como soportados / no soportados / contradichos / revisión humana.
- Los no soportados se mueven a `unsupported` (`agent.py:209-216`).
- La respuesta final es `" ".join(claim.text)` (`query.py:230`).

### 5.3 Los tres finales posibles

| Situación | Qué devuelve | Dónde |
|---|---|---|
| Hay claims válidos | Concatenación de claims + citas | `query.py:230-267` |
| Hay evidencia pero 0 claims válidos | Preguntas de rol: respuesta determinista; si no, `evidence_fallback` con 3 fragmentos | `query.py:231-263` |
| No hay evidencia primaria | `messages.insufficient_evidence` | `query.py:23-29`, `agent.py:192-198` |

Y, **siempre**, después se calculan `occurrences` con `locate()` (`chats.py:270-283`), incluso si la respuesta fue “no hay evidencia”.

---

## 6. Diagnóstico: por qué no se siente como un chat nativo

Prioridades: **P0** = rompe confianza o seguridad; **P1** = calidad de respuesta; **P2** = pulido.

### 6.1 Tabla priorizada

| # | Prio | Hallazgo | Evidencia | Impacto | Esfuerzo |
|---|---|---|---|---|---|
| 1 | P0 | Sin streaming ni progreso: hasta 7 llamadas antes del primer byte | `agent.py:157-205`, `verification.py:44`, `chat-widget.tsx:537-541` | Espera muda, abandono | L |
| 2 | P0 | `answer = " ".join(claims)`: prosa destruida | `query.py:230` | Respuestas telegráficas | M |
| 3 | P0 | `answer` y `occurrences` se contradicen | `chats.py:270-283`, `query.py:23-29` | “No hay evidencia” + 6 ubicaciones | S |
| 4 | P0 | Cambiar agente/modelo tras crear la sesión no hace nada | `chat-widget.tsx:274-276`, `chats.py:255-258` | Controles que mienten | S |
| 5 | P0 | Tools de escritura alcanzables con `ai.query`; resultados de tools sin escapar | `query.py:66`, `agent.py:60-64,171-185`, `server.py:40-51` | Escalada e inyección | M |
| 6 | P0 | `model_runs` guarda solo `{"raw_len": …}`; no se persisten claims ni `uncertainties` | `query.py:173`, `chats.py:189-192,294-302` | Turno no auditable | M |
| 7 | P0 | Con >100 mensajes se recargan los 100 **más viejos** y desaparece la respuesta nueva | `chat-widget.tsx:245`, `chats.py:189-192` | Pérdida aparente de respuestas | S |
| 8 | P0 | Grounding descarta claims correctas: `start_mmss` nunca se calcula y el umbral es alto | `answering.py:265,292`, `:173-189` | Respuestas vacías injustificadas | S |
| 9 | P1 | `k` pequeño + RRF plano + sin re-ranking | `answering.py:71,85,90,161-170` | Bajo recall | S |
| 10 | P1 | Se mezclan embeddings de modelos/versiones distintos | `answering.py:83`, `indexing.py:49-52` | Resultados aleatorios | S |
| 11 | P1 | Sin reescritura de consulta con el historial | `agent.py:140-145`, `query.py:147-156` | Follow-ups rotos | M |
| 12 | P1 | Abstención léxica bloquea preguntas respondibles | `abstention.py:46-82`, `answering.py:33` | “No hay evidencia” falso | M |
| 13 | P1 | Chunks sin solape; testimonio sin techo; no cruzan página | `chunking.py:26-29,96-121,262-293` | Evidencia de frontera perdida | M |
| 14 | P1 | Chunks estructurados se etiquetan como transcripción y no son citables | `chunking.py:297-312`, `answering.py:180-186` | Ocupan top-k sin poder citarse | M |
| 15 | P1 | Grafo fuera del retrieval | `answering.py` (no consulta grafo) | Multi-hop depende del LLM | M |
| 16 | P1 | Skills sin orden determinista; 30 tools siempre; `RESULT` truncado a 400 chars | `agent.py:43,70`, `case_tools/__init__.py` | Comportamiento irreproducible | M |
| 17 | P1 | Memoria = últimos K turnos, 800 chars, sin resumen | `chats.py:208-209`, `agent.py:141-142` | Conversaciones largas pierden contexto | M |
| 18 | P1 | Evaluación mide `legacy_retrieve(k=500)`, no producción | `scripts/run_evals.py:52,106,109` | No se puede validar ninguna mejora | M |
| 19 | P2 | Sin copiar/regenerar/reintentar/detener; autoscroll forzado; input de una línea | `chat-widget.tsx:196-199,309-312,597-606` | Paridad con chat nativo | M |
| 20 | P2 | Mensaje de insuficiencia duplicado (answer + uncertainties) | `query.py:27-29`, `chat-widget.tsx:473-480` | Ruido | S |
| 21 | P2 | API keys “encrypted” guardadas en claro | `admin.py:384-415`, `llm.py:143` | Riesgo de secretos | M |
| 22 | P2 | Versión de prompt duplicada (`v7` hardcodeado y `DEFAULT_AGENT_VERSION`) | `agent.py:23`, `builtin_agents.py:18` | Deriva de configuración | S |
| 23 | P1 | FTS sin stemming/unaccent y consulta en OR | `010_schema.sql:485`, `answering.py:59-60` | Bajo recall léxico en OCR | M |
| 24 | P2 | `has_chunks` global desactiva el fallback legacy para todo el caso si existe un chunk | `answering.py:55` | Documentos sin indexar invisibles | S |

### 6.2 Los cinco cambios con mejor relación impacto/esfuerzo

1. **Coherencia `answer` ↔ `occurrences`** (P0 #3). Si la respuesta es de insuficiencia, no calcular ni mostrar ubicaciones; si se muestran, titularlas “Menciones del término X (no responden la pregunta)”.
2. **Compositor determinista de respuesta** (P0 #2). Convertir los claims validados en párrafos/bullets con su cita inline, sin añadir hechos. No requiere otro LLM.
3. **Streaming de etapas** (P0 #1). Un SSE con `accepted → retrieving → tool → synthesizing → verifying → done` ya elimina la espera muda, aunque el texto final llegue completo.
4. **Filtro por modelo/versión de embedding + `candidate_k` amplio + re-ranker** (P1 #9-10). Es el cambio con mayor efecto medible en recall.
5. **Evaluación real** (P1 #18). Sin medir `retrieve()` y el agente, las mejoras anteriores no son verificables.

---

## 7. La captura, explicada

La imagen muestra dos turnos con la misma pregunta:

1. Primero: una frase coloquial (“Perdón un momentico, mire, se me…”) y luego “Aparece en 6 ubicaciones”.
2. Después: “No se encontró evidencia en el expediente para responder esta pregunta” y **las mismas 6 ubicaciones**.

### 7.1 Lo que sí es estructural

- `occurrences` se calcula **después** de `_run_query`, con una llamada independiente a `locate()` (`chats.py:272-283`). No depende de que la respuesta tenga claims.
- El término que se ubica **no es la pregunta**: `_content_term()` toma hasta 4 palabras de contenido (`read.py:145-150`). “¿Qué evidencia hay sobre pagos?” → `"evidencia pagos"`.
- En documentos, las cláusulas se combinan con `AND` (`read.py:1041`); en transcripciones, con `OR` (`read.py:1110`). Basta que un segmento contenga “evidencia” **o** “pagos”.
- `_format_locations()` deduplica por línea formateada `archivo+minuto+hablante` (`chats.py:127-131`): varios segmentos contiguos del mismo audio cuentan como varias “ubicaciones”.
- La UI pinta `answer`, `uncertainties` y `occurrences` como bloques separados (`chat-widget.tsx:473-513`), sin regla de coherencia.

**Conclusión estructural:** el sistema puede decir “no hay evidencia” y, a la vez, listar 6 menciones del término. No es corrupción de datos; son dos canales con contratos distintos.

### 7.2 Lo que es hipótesis (no reconstruible con el código actual)

La frase “Perdón un momentico…” **no existe en el repositorio**. No es copy de la app ni un fallback. Lo más probable es que sea un `claim` construido con un fragmento literal de ASR que pasó el grounding por ser textualmente idéntico a la evidencia. El validador comprueba solapamiento léxico y cifras, **no** que la frase responda a la pregunta ni que sea buena prosa.

En el segundo intento, el agente pudo terminar sin evidencia primaria (JSON no parseable, `done` prematuro o solo evidencia no citable) y caer en `_insufficient`. La no determinación del modelo explica que la misma pregunta dé resultados distintos.

**No se puede reconstruir el incidente** porque `model_runs.output` guarda solo `{"raw_len": …}` (`query.py:173`) y las `uncertainties` no se persisten. Ese es, por sí solo, un hallazgo P0.

---

## 8. Plan de refinamiento en tres olas

### Ola 0 — Coherencia y confianza (días)

**Objetivo:** que el chat deje de contradecirse y de perder turnos.

- [ ] Gate de coherencia: no calcular `occurrences` cuando `answer` es insuficiencia; si se muestran, etiquetar el término y agrupar por archivo.
- [ ] Eliminar duplicado de insuficiencia (`answer` + `uncertainties`).
- [ ] Arreglar paginación: últimos 100 mensajes (`ORDER BY created_at DESC` + `reverse`), no los primeros.
- [ ] Persistir `raw_text`, `claims`, `unsupported_claims` y `uncertainties` en `model_runs`/`chat_messages`.
- [ ] Calcular `start_mmss` en `_normalize_retrieved_item` y calibrar `ANSWER_MIN_GROUNDING_OVERLAP` con el set dorado.
- [ ] Mapa único de permisos por tool (lectura vs escritura) aplicado en `case_tools.execute` y en MCP.
- [ ] Enviar `agent_id`/`model_id` por mensaje **o** deshabilitar los selectores con aviso.
- [ ] Tests de regresión para la captura: misma pregunta, assert de coherencia answer/occurrences.

**Criterio de aceptación:** ninguna respuesta puede decir “no hay evidencia” y mostrar ubicaciones sin explicar que son solo menciones; ningún turno nuevo desaparece al recargar.

### Ola 1 — Calidad de recuperación y naturalidad (2–4 semanas)

**Objetivo:** que la respuesta sea útil y se lea como prosa, no como lista de claims.

- [ ] **Compositor de respuesta**: `answer` = resumen + claims en párrafos/bullets, cada uno con cita inline. Sin hechos nuevos.
- [ ] **Streaming SSE** de etapas y, después, de tokens en la síntesis.
- [ ] **Query rewriting** con historial (modelo barato) antes de retrieve.
- [ ] **`candidate_k = max(50, 4*k)`** por rama + RRF ponderado + re-ranker (cross-encoder o listwise).
- [ ] **Filtro `embedding_model`/`embedding_version`** + job de reindexado al cambiar de modelo.
- [ ] **Chunk overlap 10–15 %** y tope ~350 palabras en testimonio.
- [ ] **Dedupe por página** entre versiones OCR (`basico` vs `document_ai`), conservando la mejor.
- [ ] **FTS `spanish` + `unaccent`** (o prefijos `:*`) y reindexar `tsv`.
- [ ] **Fallback legacy por documento/medio**, no por caso: que un chunk no oculte un PDF sin indexar.
- [ ] **Abstención como señal blanda**, no como puerta previa; abstener solo con cobertura 0.
- [ ] **Evaluación real**: `retrieve()` y agente, recall@8, context precision/recall, faithfulness, abstention accuracy; gate en CI.

**Criterio de aceptación:** recall@8 medible al alza; respuestas con párrafos y cita por afirmación; follow-ups resueltos sin repetir el contexto.

### Ola 2 — Autonomía controlada (1–2 meses)

**Objetivo:** que el agente planifique y use el conocimiento relacional sin perder trazabilidad.

- [ ] **Planificador + tool calls en paralelo** (lecturas), manteniendo una sola escritura confirmada.
- [ ] **Memoria resumida** por sesión con presupuesto de tokens.
- [ ] **Grafo en retrieval**: expansión por entidades/alias y `find_person` con `normalized_name` + fuzzy.
- [ ] **Routing por tarea**: modelo barato para plan/verificación, frontier para síntesis.
- [ ] **Skills ejecutables**: `tools[]`, `parameters`, `position` y validación.
- [ ] **Panel multi-modelo de jueces** para evaluación (ver §9).
- [ ] **Paridad MCP**: agentes/skills disponibles también por MCP, o documentar que MCP es solo tools.

---

## 9. Auditoría con otros modelos de IA: cómo hacerla bien

### Lo que se hizo en esta auditoría

Se desplegaron tres agentes especialistas en paralelo:

1. **RAG / base de conocimiento**: retrieval, chunking, embeddings, abstención, grafo, evaluación.
2. **Arquitectura de IA**: agentes, skills, modelos, MCP, orquestación, seguridad.
3. **UX conversacional**: recorrido del turno, render, latencia, estados, paridad con chat nativo.

Los tres contrastaron sus afirmaciones contra código con `archivo:línea`. **No se llamaron APIs de proveedores externos**: la comparación cross-model con Claude/GPT/Gemini queda diseñada, no ejecutada.

### Protocolo recomendado (cross-model real)

1. **Congelar un set dorado** de 40–60 preguntas por expediente sintético, con:
   - `must_cite` (documento+página o media+timestamp),
   - `must_not_claim` (hechos que no están),
   - `expected_abstention` (preguntas sin evidencia),
   - `expected_style` (prosa, no concatenación).
2. **Ejecutar el mismo pipeline** (mismos prompts, tools, chunks, `k`) con 3 modelos de proveedores distintos.
3. **Juzgar con un modelo distinto al generador** y una rúbrica de 1–5:
   - faithfulness (¿cada afirmación está en la cita?),
   - citation correctness (¿la cita existe y apunta al lugar correcto?),
   - answer usefulness (¿responde la pregunta?),
   - naturalness (¿se lee como un chat?),
   - abstention (¿se abstiene cuando debe?).
4. **Reportar acuerdo entre jueces** (Krippendorff/Cohen) y no promediar a ciegas.
5. **Medir coste y latencia** por modelo y por etapa (plan, tools, síntesis, verificación).
6. **Gate de CI** ante cambios de prompt, chunking o índice.

Métrica clave que hoy falta: **faithfulness por claim**, no solo “citó algo”.

---

## 10. Riesgos y gobernanza

| Riesgo | Estado | Qué hacer |
|---|---|---|
| **Prompt injection vía expediente** | La síntesis escapa el contenido (`answering.py:205-210`), pero el loop ReAct inyecta resultados de tools sin escapar (`agent.py:60-64,184-185`) | Delimitar y escapar `RESULT`; separar datos de instrucciones; tests con OCR malicioso |
| **Escritura sin confirmación** | El prompt ordena aplicar correcciones con `confirm=true` (`agent_system.v7.md`); el flujo determinista de `chat_pending_corrections` ya existe | Unificar `propose → preview → apply` en servidor; nunca dejar que el LLM aplique |
| **Permisos por tool** | `ai.query` basta para tools de escritura en el loop interno (`query.py:66`); en MCP algunas caen a `ai.query` (`server.py:40-51`) | Mapa único tool→permiso aplicado en `case_tools` |
| **Secretos** | `encrypted_api_key` se guarda y se lee tal cual (`admin.py:384-415`, `llm.py:143`) | Cifrar con el mismo patrón de `mailer.encrypt_password` |
| **Trazabilidad** | `model_runs` no guarda salida cruda ni claims; tokens solo de la síntesis | Persistir pasos, tokens y veredictos |
| **RLS** | Bien resuelto: `tx(org, actor)` por llamada, `FORCE ROW LEVEL SECURITY`, MCP con JWT | Mantener; no abrir atajos en las mejoras |
| **Silencio de fallback** | Si el modelo de la sesión no existe, cae al global sin avisar (`llm.py:136-138`) | Devolver error explícito o advertir en la UI |

---

## 11. Anexo: mapa de archivos y piezas

### Endpoints principales

| Endpoint | Archivo | Función |
|---|---|---|
| `POST /v1/cases/{id}/query` | `routers/query.py:61` | RAG simple o agente, sin sesión |
| `POST /v1/cases/{id}/chats` | `routers/chats.py:155` | Crear sesión |
| `POST /v1/cases/{id}/chats/{sid}/messages` | `routers/chats.py:195` | Turno completo |
| `GET /v1/cases/{id}/chats/{sid}/messages` | `routers/chats.py:181` | Historial paginado |
| `/admin/agents`, `/admin/skills`, `/admin/models` | `routers/admin.py` | Configuración de IA |

### Tools del caso (30)

- **Lectura (24):** `search_case`, `search_documents`, `read_document`, `get_document_page`, `get_document_markdown`, `list_speakers`, `list_people_by_role`, `list_low_confidence_pages`, `event_relations`, `process_path`, `search_transcripts`, `search_transcript_by_time`, `get_video_segment`, `locate`, `get_file`, `list_case_files`, `graph_query`, `graph_neighbors`, `find_person`, `search_claims`, `search_facts`, `search_evidence`, `search_timeline`, `get_timeline`.
- **Escritura (6):** `correct_ocr_page`, `correct_transcript_segment`, `rename_speaker`, `merge_speakers`, `suggest_reprocess`, `reprocess_low_confidence`.

### Prompts versionados

`packages/prompts/`: `agent_system.v7.md`, `answer_question.v1.md`, `verify_answer.v1.md`, `extract_*.vN.md`, `classify_document.v1.md`, `link_*.v1.md`, `repair_json.v1.md`, `summarize_case.v1.md`.

### Documentos de referencia del repo

- `docs/SSD_Judicial_AI_Platform.md` — especificación.
- `docs/PLAN_CHAT_IA_MCP.md` — plan de chat + MCP.
- `docs/BACKLOG.md` — estado por sprint.
- `evals/golden/` — set dorado y criterios de fallo.
- `scripts/run_evals.py` — evaluación actual (legacy).

---

## 12. Qué no cambiaría

- **Aislamiento y multitenencia:** `tx(org, actor)`, RLS `FORCE`, trigger `enforce_same_org`.
- **Contrato de respuesta:** claims + citas + `unsupported_claims` + verificación semántica. Es la arquitectura correcta; hay que calibrarla, no eliminarla.
- **Chunking estructural** por secciones y metadata rica (`folio`, `part/parts`, `entities`, `ocr_mode`, `asr_mode`).
- **Tools deterministas** (`locate`, `search_transcript_by_time`, `list_people_by_role`, `role_answer`): son la ventaja real frente a un chat genérico.
- **HNSW + GIN** sobre `tsv` y handles `E1…En`.
- **MCP como superficie compartida** de las mismas tools, con JWT y RLS.

---

## 13. Cierre

El sistema ya tiene lo más difícil: **evidencia trazable y permisos por organización**. Lo que falta es la capa que convierte esa evidencia en conversación: **componer, transmitir y medir**.

Si solo se hacen tres cosas, que sean estas:

1. **Compositor de respuesta** con cita inline (no `join(claims)`).
2. **Streaming de etapas** para que la espera sea legible.
3. **Coherencia answer/occurrences** y evaluación real del pipeline.

Con eso, el chat deja de parecer un buscador que a veces se contradice y empieza a parecer un asistente jurídico que sabe lo que sabe y lo demuestra.
