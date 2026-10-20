# Contrato de implementación — Chat IA (formato, coherencia, velocidad)

Este documento es la **fuente de verdad** para los 4 workstreams. Ningún agente
puede cambiar una firma sin actualizar este contrato y avisar al integrador.

Commit base: `8618334`. Plan: `docs/PLAN_CHAT_3_PUNTOS.md`.

---

## 0. Reglas de trabajo

- No leer ni modificar `.env*`.
- No tocar archivos fuera de la lista de propiedad de cada workstream.
- No debilitar tests existentes: si una prueba codifica el bug (p. ej. exigir
  `occurrences` en una pregunta sustantiva), se actualiza al contrato nuevo.
- Todo texto visible nuevo va en `packages/i18n/{es,en}.json` (workstream B).
- `ruff check apps tests scripts` pasa limpio; la deuda preexistente se corrigió
  en la sesión de cierre (ASR, diarización, media pipeline y scripts).
- `pytest -m unit` debe pasar; los tests de integración/behavior se ejecutan con
  PostgreSQL de test (`APP_ENV=test`, `.env.test`).

---

## 1. Tipos compartidos

### 1.1 `app/services/text_terms.py` (YA CREADO — no modificar sin avisar)

```python
norm(text) -> str
raw_tokens(text, min_len=2) -> list[str]
tokens(text, min_len=3) -> list[str]          # sin stopwords/reporte
content_terms(text, min_len=3) -> list[str]
content_set(text, min_len=3) -> set[str]
coverage(claim, evidence) -> float            # términos del claim en la evidencia
overlap_with_question(claim, question) -> float
numbers(text) -> list[str]
missing_numbers(claim, evidence) -> list[str]
mmss(ms) -> str                               # "40:26" o "1:26:48"
specific_term(question) -> str | None         # "pagos"
expand_query(question) -> list[str]           # original + sinónimos jurídicos
```

### 1.2 `app/services/intent.py` (workstream A)

```python
class Intent(StrEnum):
    NAVIGATE = "navegar"
    LOOKUP = "dato"
    ANALYSIS = "analisis"
    PROCEDURAL = "procedimental"
    AGGREGATION = "agregacion"
    READ = "lectura"
    CORRECTION = "correccion"

@dataclass(frozen=True)
class IntentDecision:
    intent: Intent
    confidence: float          # 0..1
    reason: str                # explicable, sin el texto del usuario
    nav_term: str | None       # solo NAVIGATE
    source: str                # "rules" | "llm" | "override"

def classify(question: str, *, has_attachments: bool = False,
             is_correction: bool = False, history: list[dict] | None = None,
             hints: list[str] | None = None, llm=None) -> IntentDecision
```

Reglas obligatorias:

- `NAVIGATE` **solo** con señal explícita de ubicación: “dónde aparece”, “en qué
  minuto/página/archivo”, “localiza/ubicación”, “timestamp”, “busca la palabra”.
  `aparece` por sí solo NO basta. “¿Qué evidencia hay sobre pagos?” = `LOOKUP`.
- `AGGREGATION`: cuántos/quiénes + rol (juez, apoderado, testigo, perito,
  secretario, parte) o “quiénes son”.
- `PROCEDURAL`: “por qué se llegó”, “qué causó”, “qué actuación originó”,
  “antecedentes”, “cadena procesal”.
- `ANALYSIS`: contradicción, compara, relación, implica, consecuencia, analiza,
  evalúa, fortalezas/debilidades.
- `READ`: adjuntos o “lee/resume este documento”.
- `CORRECTION`: `is_correction` o frases de corrección.
- Default: `LOOKUP`.

### 1.3 `app/services/retrieval_plan.py` (workstream A)

```python
@dataclass(frozen=True)
class RetrievalPlan:
    question: str
    queries: tuple[str, ...]        # queries[0] = pregunta original
    candidate_k: int                # default max(50, 4*final_k)
    final_k: int                    # default RETRIEVAL_FINAL_K
    doc_quota: float                # default RETRIEVAL_DOC_QUOTA
    max_per_file: int
    max_per_page: int
    enable_rerank: bool
    expand_neighbors: bool
    use_graph: bool
    embedding_model: str | None
    embedding_version: str | None
    strict_embedding_version: bool

def build(question: str, decision: IntentDecision, *,
          settings=None, history: list[dict] | None = None) -> RetrievalPlan
```

`build` usa `query_rewrite.rewrite()` y `text_terms.expand_query()`. No llama al
LLM salvo que `LLM_ROUTING` tenga `query_rewrite` y la reescritura determinista
sea insuficiente.

### 1.4 `app/services/retrieval_rank.py` (workstream A)

```python
def apply(question: str, items: list[dict], *, plan: RetrievalPlan,
          intent: Intent) -> list[dict]
```

- Dedupe por `(document_id, page_number)` entre modos OCR y por
  `(media_id, segment_id)` / segmentos contiguos del mismo hablante.
- Score determinista: `0.40*cobertura + 0.25*entidad + 0.20*prior_fuente +
  0.15*proximidad`. Documentos > transcripciones para `LOOKUP` documental.
- Diversidad: máx. `max_per_file` por archivo y `max_per_page` por página; MMR
  con lambda 0.7.
- Expansión de vecinos ±1 **solo como contexto**; no añade citas nuevas.
- Devuelve como máximo `plan.final_k`.

### 1.5 `app/services/answering.py` (workstream A)

```python
def retrieve(conn, case_id, question, document_ids=None, media_ids=None,
             ocr_mode=None, asr_mode=None, *,
             plan: RetrievalPlan | None = None,
             intent: Intent | None = None) -> list[dict]
```

- Si `plan is None`, construye uno con `intent.classify` + `retrieval_plan.build`.
- Abstención **blanda**: cobertura 0 ⇒ `[]`; cobertura baja ⇒ se mantiene y se
  penaliza en el rerank (no se corta antes de buscar).
- FTS + vector con `candidate_k`; filtro `embedding_model`; `embedding_version`
  solo si `strict_embedding_version`, con fallback si deja 0 vectores.
- RRF → dedupe → `retrieval_rank.apply` → handles `E1..En`.
- `_normalize_retrieved_item` calcula `start_mmss` (`text_terms.mmss`).
- Caché: `cache.get_json/set_json` con clave
  `(case_id, sha256(question normalizada), modos, plan, embedding_model/version)`.
- `legacy_retrieve` se mantiene para casos sin chunks; no se usa en evals de
  producción.

### 1.6 `app/services/compose.py` (workstream B)

```python
@dataclass
class ComposeResult:
    answer_kind: str                  # direct|partial|insufficient|navigational|correction
    answer: str                       # Markdown final
    used_handles: list[str]           # handles E1..En en orden de primera cita
    sections: dict[str, list[str]]    # direct/evidence/limits/sources
    uncertainties: list[str]          # sin duplicados

def render(question: str, *, intent: Intent, claims: list[dict],
           evidence: list[dict], uncertainties: list[str], locale: str,
           max_citations: int | None = None,
           max_per_file: int | None = None) -> ComposeResult
```

- `claims` usa handles (`[{"text","citations":["E1"]}]`), no UUIDs.
- `direct` solo si ≥1 claim tiene `overlap_with_question >= 0.34` o llena un slot
  (quién/cuándo/cuánto). Si no, `partial`.
- `insufficient`: un solo mensaje, `used_handles=[]`, sin ubicaciones.
- Presupuesto: ≤ `MAX_VISIBLE_CITATIONS`, ≤ `MAX_CITATIONS_PER_FILE`, sin
  duplicados por página/segmento. Solo citas referenciadas.
- Marcadores `[n]` por bullet; sección “Fuentes” con `n`, archivo+página o
  archivo+minuto+hablante.
- Claim >85 % copia literal de ASR y <20 % de solape con la pregunta ⇒ se
  degrada a “Lo que dice el audio”, nunca a respuesta directa.
- Cifras/fechas: `missing_numbers` ⇒ claim no se usa (se mueve a límites).

### 1.7 `app/services/occurrences.py` (workstream B)

```python
def should_locate(decision: IntentDecision, answer_kind: str,
                  question: str) -> bool
def build(term: str, locs: list[dict], locale: str,
          max_items: int | None = None) -> tuple[str, list[dict]]
```

- Solo `NAVIGATE` o petición explícita de ubicación.
- Nunca si `answer_kind in ("insufficient","correction")`.
- Agrupa por archivo, tope `NAV_MAX_OCCURRENCES`, etiqueta “Menciones del término
  «X». No son la respuesta.”
- Dedupe por `(archivo, minuto, hablante)` y fusión de segmentos contiguos.

### 1.8 `app/services/deadline.py` (workstream B)

```python
class Deadline:
    def __init__(self, budget_ms: int | None = None)
    @property
    def expired(self) -> bool
    def remaining_ms(self) -> int
    def stage(self, name: str): ...   # context manager; registra latencia
    def can(self, stage: str, ms: int) -> bool
```

`REQUEST_DEADLINE_MS` por defecto 12000. Al agotarse: `degraded=True`, se omite
juez/rerank/vecinos y se devuelve la mejor respuesta disponible.

### 1.9 `app/services/cache.py` (workstream B)

```python
def get_json(namespace: str, key: str) -> object | None
def set_json(namespace: str, key: str, value, ttl_seconds: int) -> None
def delete_prefix(namespace: str) -> None
```

- Redis si `RATE_LIMIT_BACKEND == "redis"` y `redis` importable; si no, LRU en
  proceso con TTL. Nunca lanza excepción al llamador.

### 1.10 `agent.run_agent_query` (workstream B)

Añadir parámetros opcionales: `emit: Callable[[str, dict], None] | None`,
`allowed_tools: set[str] | None`, `verify: bool = True`.
- `emit("tool", {"name": ...})` antes de cada tool.
- `_format_tool_result` escapa el resultado (anti prompt-injection) y lo delimita
  con `<tool_result>`.
- `load_agent_prompt` usa `unnest(...) WITH ORDINALITY` + `skills.position` para
  orden determinista.
- Devuelve `tool_calls` (lista) y `degraded`.

### 1.11 `query._run_query` (workstream B)

Nuevos parámetros opcionales:
`intent_decision=None, emit=None, deadline=None, seed_items=None,
background=None, verify_mode=None`.

Flujo:
1. `emit("routing", {"intent": ...})`.
2. `emit("retrieving")` → `answering.retrieve(plan=...)` o agente.
3. `emit("synthesizing")` → 1 llamada de síntesis.
4. Validar (`parse_and_validate`).
5. `emit("validating")` → `compose.render(...)`.
6. Insertar SOLO las citas de `used_handles` (orden del compositor).
7. `model_runs.output` guarda `raw_text`, `claims`, `unsupported_claims`,
   `uncertainties`, `answer_kind`, `intent`, `degraded`, `tool_calls`.
8. Verificación semántica: `VERIFY_MODE=sync` bloquea; `async` va a
   `BackgroundTasks`; `off` no ejecuta.
9. Respuesta incluye: `answer_kind`, `intent`, `degraded`, `citations_used`,
   `occurrences_term`, `occurrences_note`.

### 1.12 `chats.send_message` + streaming (workstream B)

- `strategy="auto"` (default): router decide.
  - `AGGREGATION`: `role_answer.authoritative_evidence` + `render`, 0 LLM.
  - `NAVIGATE`: `locate` + `occurrences.build`, 0 LLM.
  - `LOOKUP`/`READ`: pipeline RAG (1 síntesis).
  - `PROCEDURAL`: `process_path` primero; si no hay, RAG.
  - `ANALYSIS`: agente (máx. 3 pasos) + síntesis.
  - `CORRECTION`: flujo determinista existente.
- `strategy="agent"`/`"rag"` fuerzan el camino histórico.
- `agent_id`/`model_id` del mensaje tienen prioridad sobre la sesión; si cambian,
  se actualiza la sesión.
- Historial: últimos K turnos + `chat_sessions.summary`.
- Paginación: últimos N (`ORDER BY created_at DESC, id DESC` + reverse).
- SSE: `POST /cases/{id}/chats/{sid}/messages/stream` con eventos
  `stage` (`accepted|routing|retrieving|synthesizing|validating|done`),
  `token` (texto compuesto ya validado), `done` (respuesta completa), `error`.
  El POST normal sigue funcionando.

### 1.13 Prompts (workstream C)

- `answer_question.v2.md`: mismo contrato JSON + `answer_kind` sugerido +
  prohibición de copiar ASR como respuesta + citas solo relevantes.
- `agent_system.v8.md`: bucle de tools + regla de no responder con fragmento
  literal + `locate` solo si el usuario pide ubicación.
- `verify_answer.v2.md`: veredictos por claim (`supported|not_supported|
  contradicted|needs_review`) con `reason` y `evidence_ids`.

Backend debe cargar v2/v8 si existen y caer a v1/v7 si no.

### 1.14 Frontend (workstream C)

- `api.postStream(path, body, onEvent)` con `fetch` + `ReadableStream`.
- Chat: muestra etapa (`stage`) mientras espera, va appendiendo `token`, y al
  `done` recarga el historial.
- Citas numeradas `[n]`, sección “Fuentes” colapsable, ocurrencias agrupadas por
  archivo y ocultas si `answer_kind === "insufficient"`.
- Envía `strategy: "auto"` + `agent_id`/`model_id` del selector en cada mensaje.
- No rompe el POST si el stream falla: fallback al endpoint normal.

### 1.15 Evaluación (workstream D)

- `evals/golden/chat_pipeline.yaml`: 40+ casos con `intent`,
  `expected_answer_kind`, `must_cite`, `must_not_cite`, `must_not_claim`,
  `max_visible_citations`, `latency_budget_ms`.
- `scripts/run_evals.py`: usa `answering.retrieve()` + `compose.render()` reales.
- `scripts/eval_matrix.py`: matriz cross-model, gates, salida JSON/MD.
- `scripts/bench_chat.py`: p50/p95 por etapa con `FakeLLM`.
- `scripts/load_chat.py`: carga HTTP opcional.
- `app/providers/recording.py`: cassettes `record/replay`.
- Capas A/B/C: `tests/unit/test_eval_metrics.py`,
  `tests/behavior/test_chat_pagos.py`, `tests/unit/test_judge.py`.
- Workflow CI que ejecute capa A/B offline.

---

## 2. Propiedad de archivos

| Workstream | Archivos |
|---|---|
| **A — Retrieval** | `services/intent.py`, `services/retrieval_plan.py`, `services/retrieval_rank.py`, `services/query_rewrite.py`, `services/graph_expand.py`, `services/retrieval_crossencoder.py`, `services/answering.py`, `services/abstention.py`, `services/indexing.py`, tests unitarios de esos módulos |
| **B — Chat/velocidad** | `services/compose.py`, `services/occurrences.py`, `services/deadline.py`, `services/cache.py`, `services/memory.py`, `services/skills.py`, `routers/query.py`, `routers/chats.py`, `services/agent.py`, `services/verification.py`, `services/case_tools/read.py`, `services/case_tools/__init__.py`, `services/role_answer.py`, `packages/i18n/*.json`, tests unitarios/integración de esos módulos |
| **C — UI/prompts** | `apps/web/src/lib/api.ts`, `apps/web/src/components/chat-widget.tsx`, `apps/web/src/components/markdown.tsx` (si hace falta), `packages/prompts/answer_question.v2.md`, `agent_system.v8.md`, `verify_answer.v2.md` |
| **D — Evals/tests** | `evals/**`, `scripts/run_evals.py`, `scripts/eval_matrix.py`, `scripts/bench_chat.py`, `scripts/load_chat.py`, `app/providers/recording.py`, `tests/behavior/test_chat_pagos.py`, `tests/unit/test_eval_*.py`, `tests/unit/test_judge.py`, `tests/unit/test_recording.py`, `.github/workflows/ci.yml` (solo añadir paso) |
| **Integrador** | `services/text_terms.py`, `core/config.py`, `schemas.py`, migraciones `0047`, contrato, integración final |

---

## 2.1 Paridad MCP (decisión)

MCP sigue siendo una **superficie de tools**, no un segundo motor de chat: expone
`case_tools` con JWT + RLS, pero no aplica router de intención, compositor ni
memoria. Los agentes y skills ejecutables viven en el chat HTTP. Documentado para
evitar expectativas falsas; una paridad completa exigiría mover el compositor a
una capa compartida.

## 3. Criterios de aceptación globales
1. “¿Qué evidencia hay sobre pagos?” ⇒ `LOOKUP`, `answer_kind` ≠ `navigational`,
   `occurrences=[]` salvo que el usuario pida ubicaciones.
2. `answer_kind=insufficient` ⇒ `claims=[]`, `citations=[]`, `occurrences=[]`,
   un solo mensaje (sin duplicado en `uncertainties`).
3. ≤ 4 citas visibles, ≤ 2 por archivo, sin duplicados.
4. Cada número de la respuesta existe en la evidencia citada.
5. `LOOKUP` p95 ≤ 8 s, `NAVIGATE` p95 ≤ 800 ms (benchmark local).
6. `pytest -m unit` verde; `pytest -m integration` verde; `ruff` verde.
7. `npm run lint` y `npm run build` verdes.

---

## 4. Comandos

```powershell
.\.venv\Scripts\python.exe -m pytest -m unit -q
.\.venv\Scripts\python.exe -m pytest -m integration -q
.\.venv\Scripts\python.exe -m ruff check apps tests scripts
cd apps/web; npm run lint; npm run build
```
