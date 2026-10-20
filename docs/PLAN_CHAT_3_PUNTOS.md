# Chat IA: plan para formato, coherencia y velocidad

**Tres puntos, una sola causa:** el sistema no decide *qué tipo de pregunta* está respondiendo ni *cuánto vale cada cita*. El modelo no es el problema; la tubería tampoco está medida.

Este plan es deliberadamente **agnóstico al modelo**. Toda garantía de formato, coherencia y latencia vive en código Python (router, embudo, compositor, validador, deadline). El LLM solo propone *claims*; nunca decide el formato final ni cuántas citas mostrar.

Referencias al código en el commit `8618334`.

---

## 0. Resumen ejecutivo

| Punto | Qué falla hoy | Qué lo resuelve | Se mide con |
|---|---|---|---|
| **1. Formato y citas** | `answer = " ".join(claims)` (`query.py:230`); `occurrences` se calcula siempre y aparte (`chats.py:270-283`); no hay presupuesto de citas | Router de intención + embudo de recuperación + compositor determinista + política de citas | `citation_precision`, `noise_ratio`, `max_citations`, tests de formato |
| **2. Coherencia y grounding** | Un canal puede decir “no hay evidencia” y el otro listar 6 ubicaciones; el grounding acepta un fragmento literal de ASR; no se puede reconstruir el turno | Invariantes de coherencia en código + evaluación en 3 capas (determinista, retrieval, juez semántico portable) + fixtures de incidentes | `faithfulness`, `citation_correctness`, `abstention_accuracy`, `coherence_pass` |
| **3. Velocidad** | Hasta 7 llamadas secuenciales antes del primer byte (`agent.py:157-205`, `verification.py:44`, `chats.py:276`); sin streaming | Presupuesto de latencia + pipeline por intención + verificación fuera de la ruta crítica + streaming + caché | p50/p95 por etapa, TTFT, % de turnos dentro de deadline |

**La captura de “pagos” es un caso de routing, no de datos.** “¿Qué evidencia hay sobre pagos?” es una pregunta de *dato*, no de *navegación*. El sistema la trató como navegación: buscó el término `"evidencia pagos"` y devolvió 6 menciones de audio. La respuesta correcta debía ser un párrafo con 1–3 citas relevantes (o “no hay evidencia de pagos” si no la hay). Las 6 ubicaciones no son la respuesta; como máximo son un anexo.

---

## 1. Punto 1 — Formato y citas

### 1.1 La causa exacta

Hoy hay un solo camino, y siempre termina en los mismos tres bloques:

1. El agente hace hasta 5 pasos de tool-calling libre (`agent.py:157-185`).
2. La síntesis devuelve `claims[]` (`answering.py:272-298`).
3. `query.py:230` concatena los claims con un espacio.
4. `chats.py:272-283` ejecuta `locate()` con `_content_term()` (`read.py:145-150`), que convierte “¿Qué evidencia hay sobre pagos?” en `"evidencia pagos"`.
5. La UI pinta `answer`, `uncertainties` y `occurrences` como bloques independientes (`chat-widget.tsx:473-513`).

No hay ningún punto donde el sistema decida: *esta pregunta no pide ubicaciones*, *esta cita no aporta*, *esto no responde*, *sobran 5 de las 6 citas*.

### 1.2 La solución: cinco tuberías, no una

Un **router de intención determinista** (con fallback opcional a un modelo barato) clasifica cada turno antes de recuperar. Cada intención tiene su propio camino, su presupuesto de citas y su formato de salida.

| Intención | Señales | Tubería | Llamadas LLM | Salida |
|---|---|---|---|---|
| `navegar` | “dónde aparece”, “en qué minuto”, “busca la palabra”, “en qué página” | `locate()` acotado + agrupación por archivo | 0 | Lista de menciones, nunca “respuesta” |
| `dato` | “qué dice”, “cuánto”, “quién”, “cuándo”, “qué evidencia hay sobre X” | rewrite opcional → híbrido con cuotas → rerank → 1 síntesis → compositor | 1 | Párrafo directo + 1–4 citas |
| `analisis` | “contradicciones”, “cómo se relaciona”, “qué implica”, “compara” | plan → 2–3 lecturas en paralelo → síntesis → verificación | 2–3 | Respuesta estructurada + límites |
| `procedimental` | “por qué se llegó a”, “qué causó”, “qué actuación originó” | `process_path` / `event_relations` primero | 1 | Cadena cronológica con EV-XXXX |
| `agregacion` | “cuántos jueces”, “quiénes son los apoderados” | `role_answer` determinista | 0 | Lista fija + total + citas |
| `lectura` | adjuntos `@`, “lee este documento” | retrieval acotado a los ids | 1 | Resumen con páginas |
| `correccion` | “corrige”, “el hablante es”, “sí/cancelar” | flujo determinista existente (`chats.py:227-243`) | 0 | Confirmación |

Regla de oro: **`navegar` no es la respuesta por defecto**. Solo se activa con señales explícitas de ubicación. Una pregunta sustantiva nunca debe terminar en una lista de timestamps.

### 1.3 El embudo de recuperación

El problema de las citas irrelevantes no se arregla en la UI; se arregla antes de que lleguen al prompt.

```
pregunta
  → normalizar (acentos, sinónimos jurídicos, entidades)
  → plan de retrieval (queries, alcance, cuotas por fuente)
  → candidatos: candidate_k = max(50, 4*k) por rama
  → dedupe (misma página entre OCR básico/document_ai; mismo segmento contiguo)
  → rerank (cobertura de términos + entidad + prior de fuente + proximidad)
  → diversidad (máx. 2 chunks por archivo, máx. 2 por página; MMR)
  → expansión de vecinos ±1 (solo contexto, no nuevas citas)
  → top 8–12 evidencias
  → síntesis
  → validación
  → compositor con presupuesto de citas
```

Cambios concretos:

- **`candidate_k` amplio + reranker.** Hoy `k` es pequeño y el RRF es casi plano (`answering.py:90,161-170`). Con 50 candidatos por rama y un reranker, el top 8 mejora sin cambiar de modelo.
- **Reranker agnóstico al modelo.** Primera versión determinista y sin dependencias: `0.40 * cobertura de términos + 0.25 * coincidencia de entidad/persona + 0.20 * prior de fuente (documento > transcripción para preguntas documentales) + 0.15 * proximidad`. Después, opcional: cross-encoder local multilingüe (`bge-reranker-v2-m3`) como dependencia separada, con fallback al determinista.
- **Cuotas por fuente.** Evitar que una transcripción larga inunde el top-k de una pregunta documental: 60 % documentos / 40 % transcripciones, ajustable por intención.
- **Filtro de embeddings.** `WHERE embedding_model = :current AND embedding_version = :current` (`answering.py:83`; los campos ya existen en `010_schema.sql:483-487`). Hoy se mezclan espacios vectoriales.
- **Dedupe entre modos OCR.** Misma `(document_id, page_number)` en `basico` y `document_ai`: conservar la mejor puntuación, no ambas.
- **`_content_term()` corregido.** Eliminar palabras de reporte (“evidencia”, “prueba”, “información”, “documento”) y quedarse con el sustantivo más específico: `"pagos"`. En documentos, `AND` solo si hay 2+ términos útiles; en transcripciones, `OR` con ranking, no `OR` plano (`read.py:1041,1110`).
- **Abstención como señal, no como puerta.** `abstention.should_abstain()` devuelve `[]` antes de buscar (`answering.py:33`). Debe degradar el ranking, no bloquear; abstenerse solo con cobertura 0.

### 1.4 El compositor determinista

Nuevo módulo `apps/api/app/services/compose.py`. No llama al LLM. Recibe claims validados, citas y la intención, y produce Markdown con un contrato fijo.

**Contrato de salida:**

```json
{
  "answer_kind": "direct | partial | insufficient | navigational | correction",
  "answer": "markdown listo para pintar",
  "citations_used": ["<citation_id>"],
  "sections": {
    "direct": "...",
    "evidence": ["..."],
    "limits": ["..."],
    "sources": ["..."]
  }
}
```

**Reglas del compositor:**

1. `direct` solo si al menos un claim responde a la pregunta. Heurística determinista: solapamiento con los términos de contenido de la pregunta ≥ umbral **o** slot de intención lleno (quién/cuándo/cuánto). Si no, `partial`.
2. `partial` se titula “Encontré material relacionado, pero no responde directamente” y muestra 2–3 fragmentos cercanos. Nunca se presenta como respuesta.
3. `insufficient` es **un solo mensaje**, sin claims, sin citas y sin ocurrencias. Se elimina el duplicado `answer + uncertainties` (`query.py:27-29`, `chat-widget.tsx:473-480`).
4. **Presupuesto de citas:** máximo 4 visibles (configurable), máximo 2 por archivo, cero duplicados por `(document_id, page)` o `(media_id, segment_id)`. Se muestran solo las citas referenciadas por un claim.
5. **Cita inline:** cada bullet termina en `[n]`; la sección “Fuentes” resuelve `[n]` a archivo+página o archivo+minuto+hablante. Esto reemplaza la bolsa de chips suelta.
6. **Nada de ASR crudo como respuesta.** Si un claim es >85 % copia literal de un segmento y su solapamiento con la pregunta es <20 %, se degrada a cita textual bajo “Lo que dice el audio”, no a la respuesta directa.
7. **Límites explícitos:** si hay versiones contradictorias, se dice; si la evidencia es de parte, se atribuye (“según la demanda”, “según el testigo”); si no hay documento, se declara.
8. **Cifras, fechas y nombres** se validan contra la evidencia citada antes de renderizar. Si falta un número, el claim no se usa (ya existe base en `answering.py:252`).

**Forma final (ejemplo):**

```markdown
El expediente no contiene un documento que acredite el pago de $80.000.000. Lo que sí consta
es la mención del pago en la demanda [1] y su discusión en la audiencia [2].

**Evidencia**
- La demanda afirma que el pago se realizó el 13/12/2024 [1].
- En la audiencia se discute la existencia del pago, sin aportar soporte [2].

**Límites**
- La afirmación de la demanda es una alegación de parte, no un hecho probado.
- No se encontró comprobante bancario en los documentos indexados.

**Fuentes**
[1] demanda.pdf · p. 4 (folio 12)
[2] 0065GrabacionAudiencia13Dic2024.mp4 · 1:26:48 · Alba Lucy Cock Álvarez
```

Eso responde, cita poco, distingue alegación de prueba y no llena la pantalla de ruido.

### 1.5 Política de `occurrences`

`occurrences` deja de ser un cálculo universal y pasa a ser una **salida de la intención `navegar`**.

- Se calcula solo si: la intención es `navegar`, o el usuario pide explícitamente “dónde aparece / en qué minuto / en qué página”.
- Nunca si `answer_kind` es `insufficient` o `correction`.
- Tope de 8, agrupadas por archivo:
  `0065Grabacion…mp4 — 5 menciones: 40:26, 43:29, 1:26:48, 1:27:14, 1:27:22`
- Etiqueta obligatoria: “Menciones del término «pagos». No son la respuesta.”
- Dedupe por `(archivo, minuto, hablante)` y fusión de segmentos contiguos del mismo hablante.
- Si el término no es informativo (menos de 3 caracteres, stopword o palabra de reporte), no se calcula.

Con esto, la captura deja de ser posible por construcción: una respuesta de insuficiencia **no puede** llevar ubicaciones.

### 1.6 Cambios por archivo

| Archivo | Cambio |
|---|---|
| `services/intent.py` (nuevo) | `classify(question, history, hints) -> Intent`; reglas deterministas + fallback LLM barato con JSON validado |
| `services/retrieval_plan.py` (nuevo) | `RetrievalPlan`: queries, scopes, cuotas, `candidate_k`, rerank, dedupe |
| `services/retrieval_rank.py` (nuevo) | reranker determinista + MMR + diversidad + dedupe OCR |
| `services/compose.py` (nuevo) | compositor, presupuesto de citas, `answer_kind`, reglas de ASR |
| `services/answering.py` | `retrieve(..., plan)`; filtro `embedding_model/version`; cuotas; abstención blanda |
| `routers/query.py` | sustituir `" ".join(claims)` por `compose.render(...)`; propagar `answer_kind` |
| `routers/chats.py` | `occurrences` solo si `intent=navigate`; agrupar y topar; no duplicar insuficiencia |
| `services/case_tools/read.py` | `_content_term()` sin palabras de reporte; `locate()` con ranking y dedupe |
| `apps/web/src/components/chat-widget.tsx` | pintar `[n]` inline; “Fuentes” colapsable; ocurrencias agrupadas; ocultar si `answer_kind=insufficient` |
| `packages/prompts/answer_question.v2.md` | claims atómicos + `answer_kind` sugerido; sin prosa libre obligatoria |

---

## 2. Punto 2 — Coherencia y respuestas basadas en la data

### 2.1 Invariantes verificables (no aspiraciones)

Estas reglas se implementan en código y se prueban; no dependen del modelo:

1. Toda cita visible está referenciada por al menos un claim.
2. Toda cita de un claim resuelve a una fila de `citations` con fuente existente.
3. `answer_kind=insufficient` ⇒ `claims=[]`, `citations=[]`, `occurrences=[]`.
4. `answer_kind=navigational` ⇒ no hay claims de respuesta; las ubicaciones van etiquetadas.
5. Todo número/fecha/nombre de la respuesta aparece en la evidencia citada.
6. Cero duplicados `(document_id, page)` / `(media_id, segment_id)`.
7. Citas visibles ≤ `MAX_VISIBLE_CITATIONS`.
8. Ningún claim es copia literal de ASR sin marca de cita textual.
9. Todos los handles citados existen en la evidencia usada para sintetizar.
10. La misma pregunta, mismo índice y temperatura 0 produce el mismo `answer_kind` y un conjunto de citas estable (±1).

### 2.2 Evaluación en tres capas

**Capa A — determinista (sin LLM, rápida, obligatoria en CI).**
Valida las 10 invariantes, el esquema JSON, la resolución de citas, los números, los duplicados, el presupuesto y la coherencia `answer/occurrences`. Cubre ~60–70 % de lo que hoy se le pide a un juez. Coste: milisegundos.

**Capa B — retrieval (sin LLM).**
`recall@k`, `MRR`, `nDCG@k`, `context_precision`, `context_recall`, `citation_recall`, `citation_precision`, `noise_ratio` (citas irrelevantes por respuesta). Se calcula por intención, no en agregado: un sistema puede tener buen recall global y fallar justo en `dato`.

**Capa C — juez semántico portable.**
Solo para lo que el código no puede decidir: *¿el claim se sigue de la cita?*, *¿la respuesta responde?*, *¿se lee natural?*. Usa el mismo adaptador de proveedores (`get_llm_for_model`, `llm.py:126-152`), un modelo **distinto** al generador, temperatura 0, rúbrica JSON y 3 ejecuciones con mayoría.

### 2.3 Set dorado: añadir lo que hoy no se mide

El formato actual (`evals/golden/example.jsonl`) tiene `must_cite`, `must_abstain` y `forbidden_statements`. Le faltan las dos métricas que explican la captura: **citas que no debían aparecer** y **tipo de respuesta esperado**.

```json
{
  "id": "G-010",
  "case_ref": "11001310300120240012300",
  "locale": "es",
  "question": "¿Qué evidencia hay sobre pagos?",
  "intent": "dato",
  "expected_answer_kind": "direct|partial|insufficient",
  "must_cite": [{"filename": "demanda.pdf", "page": 4}],
  "must_not_cite": [{"filename": "0065GrabacionAudiencia13Dic2024.mp4"}],
  "must_contain": ["pago"],
  "must_not_claim": ["el pago está probado judicialmente"],
  "expected_abstention": false,
  "max_visible_citations": 4,
  "latency_budget_ms": 6000
}
```

`must_not_cite` es la métrica que faltaba: hoy el sistema pueden citar cinco documentos irrelevantes y aun así “pasar” porque citó el correcto.

Set mínimo: **40–60 preguntas** balanceadas por intención (12 dato, 8 navegar, 8 análisis, 6 procedimental, 6 agregación, 5 sin evidencia, 5 adversariales). Cada una con `must_cite`, `must_not_cite`, `must_not_claim` y `expected_answer_kind`.

### 2.4 Juez portable: cómo hacerlo con cualquier modelo

- **Adaptador único.** `judge(prompt, schema)` usa `get_llm_for_model`; funciona con Gemini, DeepSeek, OpenAI, Claude, Kimi y `custom` sin cambiar el juez.
- **Independencia.** El juez no puede ser el mismo modelo que generó. Si no hay otro disponible, se marca `same_family=true` y no cuenta para el gate.
- **Rúbrica cerrada.** `faithfulness`, `citation_correctness`, `usefulness`, `naturalness`, `abstention`, cada una 1–5, más `verdict` y `reasons[]`. Salida JSON validada; si no parsea, se reintenta con `repair_json` y, si falla, se registra `judge_error` (no se promedia).
- **Acuerdo.** Antes de confiar en el juez, se etiquetan 50 respuestas a mano y se mide Cohen's κ con el juez. Gate: κ ≥ 0.6. Si no, se ajusta la rúbrica; no se baja el umbral.
- **Coste.** El juez corre en nightly y en el gate de cambios de prompt/chunking/índice, no en cada turno de producción.

### 2.5 Record/replay: pruebas rápidas y sin API keys

Añadir un middleware al adaptador LLM que grabe `(hash(system), hash(user), modelo, respuesta)` en `evals/cassettes/*.jsonl` y lo reproduzca en CI. Esto permite:

- ejecutar el set dorado sin credenciales ni coste;
- comparar el mismo pipeline con 5 modelos sobre las mismas respuestas grabadas;
- reproducir el incidente de “pagos” como test de regresión;
- medir latencia de la tubería sin la variabilidad de la red (la latencia del proveedor se mide aparte).

### 2.6 Matriz cross-model y gate

`scripts/eval_matrix.py --models gemini:…,deepseek:…,openai:…,anthropic:…,kimi:… --judge … --golden evals/golden/`

Salida: una fila por modelo con las métricas A/B/C, latencia p50/p95 y coste. El gate falla si:

- `coherence_pass < 100 %` (es determinista, no admite excusa);
- `citation_precision < 0.85`;
- `faithfulness < 0.90`;
- `abstention_accuracy < 0.90`;
- `p95_latency > budget` para `dato`.

Un modelo nuevo entra al producto solo si pasa la matriz. Así “funciona con cualquier modelo” deja de ser una promesa y pasa a ser un contrato verificable.

### 2.7 Tests de regresión del incidente

`apps/api/tests/behavior/test_chat_pagos.py`:

1. Misma pregunta, evidencia con menciones de audio y un documento sin soporte de pago → `answer_kind in (partial, insufficient)`, `occurrences` vacío, `citations_used` ≤ 2, ninguna cita de audio si no responde.
2. Pregunta “¿en qué minuto se habla de pagos?” → `answer_kind=navigational`, ubicaciones agrupadas, sin claims.
3. Evidencia vacía → un solo mensaje de insuficiencia, sin duplicado en `uncertainties`.
4. Claim con cifra inventada → descartado; la respuesta no la contiene.
5. Claim copiado de ASR sin relación con la pregunta → no es la respuesta directa.

---

## 3. Punto 3 — Velocidad

### 3.1 Dónde se va el tiempo

Hoy, en el peor caso:

| Etapa | Llamadas | Dónde |
|---|---|---|
| Loop ReAct | hasta 5 LLM secuenciales | `agent.py:157-185` |
| Síntesis | 1 LLM | `agent.py:204` |
| Verificación semántica | 1 LLM | `verification.py:44` |
| `locate` | 1 consulta pesada (k=300) | `chats.py:276-278` |

Siete llamadas antes de la primera palabra. Y la UI solo tiene un spinner (`chat-widget.tsx:537-541`).

### 3.2 Presupuesto de latencia (contrato)

| Etapa | p50 | p95 | Nota |
|---|---|---|---|
| Router de intención | 5 ms | 20 ms | determinista |
| Retrieval híbrido | 250 ms | 600 ms | 1 embedding + 1–2 SQL |
| Rerank + diversidad | 80 ms | 250 ms | determinista; cross-encoder opcional |
| Síntesis (primer token) | 800 ms | 2.000 ms | depende del proveedor |
| Validación + compositor | 15 ms | 40 ms | Python puro |
| **Total `dato`** | **≤ 4 s** | **≤ 8 s** | sin juez en la ruta crítica |
| **Total `navegar`** | **≤ 300 ms** | **≤ 800 ms** | 0 LLM |

`REQUEST_DEADLINE_MS` (nuevo, por defecto 12.000) se propaga por etapa. Si se agota: se devuelve la mejor respuesta parcial disponible, `degraded=true`, y se registra la etapa que falló. Nunca se deja al usuario esperando sin límite.

### 3.3 Cambios que reducen latencia de verdad

1. **Pipeline por intención en lugar de ReAct por defecto.**
   El chat usa `strategy="agent"` siempre (`chat-widget.tsx:284-290`). Para `dato`, un plan determinista de 1–2 búsquedas + 1 síntesis es más rápido y más predecible. ReAct se reserva para `analisis`.
2. **Lecturas en paralelo.**
   Si el plan pide documentos y transcripciones, se ejecutan con dos conexiones y `ThreadPoolExecutor` (o `asyncio.gather` si se migra el router). Hoy son secuenciales.
3. **Juez fuera de la ruta crítica.**
   La verificación semántica se ejecuta después de responder (post-response) y se guarda en `model_runs`. En la ruta solo queda la validación determinista. Si un claim tiene cifra o nombre propio, se puede verificar de forma síncrona y barata.
4. **`locate` solo para `navegar`.**
   Elimina una consulta `k=300` en cada turno sustantivo.
5. **Streaming SSE de etapas y tokens.**
   Nuevo `POST /chats/{sid}/messages:stream` (o `Accept: text/event-stream`) con eventos:
   `accepted → routing → retrieving → reranking → synthesizing → validating → done`, y `token` durante la síntesis. La UI pinta el progreso desde el primer evento. Aunque el texto final llegue completo, la espera deja de ser muda.
6. **Caché en dos niveles.**
   - Retrieval: clave `(case_id, sha256(pregunta normalizada), modos, embedding_version, index_epoch)`, TTL 10 min, Redis si está configurado (`RATE_LIMIT_BACKEND=redis`) o LRU en proceso.
   - Embeddings: clave `(texto, modelo)`, TTL largo.
   - Respuesta: solo para pregunta idéntica + mismo `evidence_hash`, TTL corto; nunca para preguntas con adjuntos.
7. **Menos tokens de entrada.**
   Top 8–12 tras rerank, snippets de 600–900 caracteres, sin duplicados. El prompt de síntesis baja de decenas de miles de tokens a unos pocos miles; eso reduce latencia y coste en todos los proveedores.
8. **Consultas SQL más cortas.**
   Medir con `EXPLAIN (ANALYZE, BUFFERS)`; unir FTS y vector en una sola ida y vuelta cuando sea posible; revisar `hnsw.ef_search`; índices GIN/HNSW ya existen.
9. **Circuit breaker por proveedor.**
   Timeout por llamada (`LLM_TIMEOUT_SECONDS`), reintento acotado, y caída a un modelo rápido configurado en `LLM_ROUTING` si el primario supera el presupuesto. Hoy un proveedor lento consume el turno entero.

### 3.4 Cómo se mide (y se evita el autoengaño)

- **Instrumentación por etapa.** `metrics.track_stage()` ya existe (`metrics.py:47`); añadir spans con `turn_id`, `intent`, `model`, `tokens`, `cache_hit`, `degraded`.
- **TTFT y total.** No basta el total: el usuario percibe el primer token.
- **Benchmark reproducible.** `scripts/bench_chat.py` con 40 preguntas del set dorado, `FakeLLM` para aislar retrieval/compositor y proveedor real para latencia de red.
- **Carga.** 10 usuarios concurrentes durante 5 minutos; mirar p95, pool de conexiones, CPU del reranker y errores 429.
- **Comparación por modelo.** La misma prueba con Gemini Flash, DeepSeek, GPT, Claude y Kimi; se guarda `evals/latency/<provider>.json`. El router elige el modelo según el presupuesto, no según la marca.
- **Presupuesto en CI.** Un PR que suba el p95 de retrieval >20 % falla el gate.

---

## 4. Cómo funciona con cualquier modelo

### 4.1 El principio

**El modelo propone; el sistema dispone.** Todo lo que el usuario ve (formato, número de citas, agrupación, abstención, coherencia) se decide en Python. El modelo solo devuelve claims y citas; si no cumple el contrato, se descarta o se repara.

### 4.2 Contrato de capacidades

Interfaz mínima del adaptador (ampliar `providers/base.py`):

```python
class LLMProvider(Protocol):
    name: str
    model: str
    def complete(self, system: str, user: str) -> LLMResult: ...
    def stream(self, system: str, user: str) -> Iterator[str]: ...   # opcional
    def capabilities(self) -> Capabilities: ...                     # json_mode, tools, context, streaming
```

- Si el proveedor no tiene streaming, el SSE sigue funcionando a nivel de etapas y el texto llega en un evento `token` final.
- Si no tiene JSON mode, se usa el contrato de texto + `repair_json` (ya existe en `packages/prompts/`).
- Si no tiene tool calling nativo, el loop parsea texto (ya es el caso en `agent.py:160-163`).
- Si el contexto es corto, el embudo recorta evidencias y el compositor sigue igual.

### 4.3 Matriz de capacidades y degradación

| Capacidad | Uso | Si falta |
|---|---|---|
| Streaming | TTFT | etapas SSE + token final |
| JSON mode | salida estructurada | prompt + `repair_json` + validación |
| Tool calling | loop ReAct | parser de texto actual |
| Contexto largo | muchas evidencias | top 8 + recorte por presupuesto |
| Temperatura 0 | estabilidad | fijar `seed` si existe; si no, 3 muestras y mayoría |

### 4.4 Reglas de portabilidad de prompts

- Un solo prompt canónico, sin sintaxis de proveedor.
- Temperatura 0 por defecto en router, síntesis y juez.
- Esquema JSON validado en servidor; nunca se confía en “el modelo obedece”.
- Prohibido meter lógica de negocio en el prompt: el prompt describe el contrato, no lo garantiza.
- Versionado `vN+1`; el set dorado se ejecuta contra la versión nueva antes de promoverla.

---

## 5. Orden de implementación

### Fase 0 — Cortar el ruido (2–4 días)

- [x] `intent.py` con reglas deterministas y las 7 intenciones.
- [x] `compose.py` con `answer_kind`, presupuesto de citas y reglas de ASR.
- [x] `query.py`: reemplazar `" ".join(claims)` por el compositor.
- [x] `chats.py`: `occurrences` solo en `navegar`; agrupar, topar, etiquetar.
- [x] `read.py`: `_content_term()` sin “evidencia/prueba”; `locate()` con ranking.
- [x] Tests de coherencia + fixture del incidente “pagos”.
- [x] Medir el antes: 20 preguntas, latencia y número de citas.

**Criterio de aceptación:** la pregunta de “pagos” ya no puede producir “no hay evidencia” + 6 ubicaciones; ninguna respuesta muestra más de 4 citas; la respuesta es prosa.

### Fase 1 — Embudo y evaluación (1–2 semanas)

- [x] `retrieval_plan.py` + `retrieval_rank.py`: `candidate_k`, cuotas, dedupe OCR, rerank determinista, MMR.
- [x] Filtro `embedding_model/version`.
- [x] Set dorado de 40–60 preguntas con `must_not_cite` y `expected_answer_kind`.
- [x] Capas A y B en CI; capa C nightly con juez portable.
- [x] Record/replay de cassettes.
- [x] `scripts/eval_matrix.py` y primera matriz con 3 modelos.

**Criterio de aceptación:** `citation_precision ≥ 0.85`, `coherence_pass = 100 %`, `recall@8` medido y comparado contra la línea base.

### Fase 2 — Latencia y streaming (1–2 semanas)

- [x] SSE de etapas y tokens; UI con progreso real.
- [x] Juez post-response; `locate` condicionado; lecturas en paralelo.
- [x] `REQUEST_DEADLINE_MS` + degradación + circuit breaker.
- [x] Caché de retrieval/embeddings.
- [x] `scripts/bench_chat.py` + prueba de carga + presupuesto en CI.

**Criterio de aceptación:** `dato` p95 ≤ 8 s, `navegar` p95 ≤ 800 ms, TTFT ≤ 2 s, 0 turnos sin deadline.

### Fase 3 — Calidad avanzada (opcional, 2–4 semanas)

- [x] Query rewriting con historial (modelo barato, JSON validado).
- [x] Cross-encoder local como reranker opcional.
- [x] Expansión por grafo/entidades.
- [x] Memoria resumida por sesión.
- [x] Skills ejecutables (tools, parámetros, orden determinista).

---

## 6. Qué no hacer

- **No confiar el formato al prompt.** “Responde en párrafos y cita solo lo relevante” no es una garantía; el compositor sí.
- **No usar otro LLM para componer la respuesta final.** Añade latencia, coste y una nueva fuente de alucinación. El compositor es determinista.
- **No bajar el umbral de grounding para “que responda”.** Se cambia el contrato: `partial` explícito en vez de claim no fundamentado.
- **No medir con `legacy_retrieve(k=500)`.** Es lo que hace hoy `scripts/run_evals.py:52`; no representa producción.
- **No comparar modelos por “sensación”.** La matriz con set dorado y juez independiente es la única comparación válida.
- **No activar ReAct por defecto.** Es el camino más lento y el que más varía entre modelos.

---

## 7. Definición de “hecho”

1. La misma pregunta produce el mismo *tipo* de respuesta y el mismo formato en Gemini, DeepSeek, OpenAI, Claude y Kimi.
2. Ninguna respuesta sustantiva muestra más de 4 citas; ninguna cita es duplicada ni irrelevante según el set dorado.
3. “No hay evidencia” nunca convive con ubicaciones; las menciones van etiquetadas y agrupadas.
4. Cada afirmación tiene cita resoluble; cada número aparece en la evidencia.
5. `dato` responde en p95 ≤ 8 s; `navegar` en ≤ 800 ms; el usuario ve progreso desde el primer segundo.
6. El pipeline se evalúa con `retrieve()` y el compositor reales, no con el retrieval legacy.
7. Un modelo nuevo solo se habilita si pasa la matriz cross-model.

---

## 8. Anexo: ejemplo de decisión para la captura

**Pregunta:** “¿Qué evidencia hay sobre pagos?”

**Antes**

```
Perdón un momentico, mire, se me…
Aparece en 6 ubicaciones:
0065Grabacion…mp4 · 40:26 · Alba Lucy Cock Álvarez
…
```

**Después (si hay soporte documental)**

```markdown
El expediente contiene una alegación de pago, pero no un comprobante que la acredite.
La demanda afirma que el pago se hizo el 13/12/2024 [1]; en la audiencia se discute
sin aportar soporte [2].

**Límites**
- La afirmación de la demanda es de parte, no un hecho probado.
- No se encontró comprobante bancario entre los documentos indexados.

**Fuentes**
[1] demanda.pdf · p. 4
[2] 0065Grabacion…mp4 · 1:26:48 · Alba Lucy Cock Álvarez
```

**Después (si no hay soporte)**

```markdown
No encontré en el expediente un documento que acredite pagos. La única referencia es
una mención en la audiencia, que no constituye prueba del pago.

**Para acotar:** puedo buscar “comprobante”, “transferencia” o “consignación”, o revisar
las páginas con menor confianza de OCR.
```

Cero ubicaciones sueltas, cero citas decorativas, una respuesta que se lee como un asistente jurídico.
