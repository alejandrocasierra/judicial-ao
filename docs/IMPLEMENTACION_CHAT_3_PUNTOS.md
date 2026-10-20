# ImplementaciÃ³n del plan de Chat IA â€” cierre de las 4 fases

**Commit base:** `8618334`  
**Fecha:** 2026-10-19  
**Alcance:** formato/citas, coherencia/grounding, latencia/streaming y fase avanzada
(rewrite, grafo, memoria, skills ejecutables, evaluaciÃ³n cross-model).


> **Cierre ampliado 2026-10-20:** además de las 4 fases, se cerraron los
> bloqueantes B1–B5 y los 17 pendientes del plan/auditoría. Las migraciones
> **0047–0050** están aplicadas en dev, quality y producción. El índice se
> reconstruyó con el proveedor local determinista `local-ngram-1024` porque la
> cuenta de Gemini devolvió `402 RESOURCE_EXHAUSTED`; el cambio a Gemini es una
> variable de entorno + `scripts/reindex_embeddings.py --all`. La suite completa
> queda en **1.159 passed**, `ruff` en **All checks passed**, y las imágenes
> nuevas aún deben desplegarse (B2). Ver §6 y §7.

---

## 1. Resultado por fase

### Fase 0 â€” Cortar el ruido

| Entregable | Estado | Evidencia |
|---|---|---|
| Router de intenciÃ³n (7 intenciones) | âœ… | `services/intent.py`; â€œÂ¿QuÃ© evidencia hay sobre pagos?â€ â†’ `dato`, â€œÂ¿DÃ³nde se habla de pagos?â€ â†’ `navegar` |
| Compositor determinista | âœ… | `services/compose.py`: `direct/partial/insufficient/navigational/correction`, â‰¤4 citas, â‰¤2 por archivo, sin duplicados |
| `query.py` sin `" ".join(claims)` | âœ… | `compose.render()` + citas solo de `used_handles` |
| `occurrences` solo en navegaciÃ³n | âœ… | `services/occurrences.py` + gate en `chats.py`; nunca en `insufficient`/`correction` |
| `_content_term` sin palabras de reporte | âœ… | `text_terms.specific_term`: `"pagos"` â†’ `pago`; `"pagarÃ© 001"` â†’ `pagare 001` |
| Tests de coherencia + fixture â€œpagosâ€ | âœ… | `tests/behavior/test_chat_pagos.py`, `tests/integration/test_chat_pipeline.py` |

**Antes/despuÃ©s real del caso â€œpagosâ€ (FakeLLM, seed):**

- Antes: `occurrences` con 6 timestamps + respuesta vacÃ­a/eco de ASR.
- Ahora: `answer_kind=partial`, 1 cita (`02_contestacion.pdf p.1`), `occurrences=[]`.
- NavegaciÃ³n explÃ­cita: `answer_kind=navigational`, 4 menciones agrupadas en 3 archivos, sin claims.

### Fase 1 â€” Embudo y evaluaciÃ³n

| Entregable | Estado | Evidencia |
|---|---|---|
| `candidate_k`, cuotas, dedupe OCR, rerank, MMR | âœ… | `services/retrieval_plan.py`, `retrieval_rank.py` |
| Filtro `embedding_model/version` | âœ… | `answering.retrieve`; fallback si la versiÃ³n estricta deja 0 |
| Fallback legacy por documento/medio | âœ… | `_supplement_unindexed`: un chunk de audio ya no oculta PDFs sin indexar |
| Set dorado 50 casos | âœ… | `evals/golden/chat_pipeline.yaml` |
| Capas A/B en CI, C nightly | âœ… | `scripts/eval_offline.py`, `run_evals.py`, `eval_matrix.py`, CI |
| Record/replay | âœ… | `providers/recording.py`; 19/19 cassette hits en la prueba de D |
| Matriz cross-model | âœ… | `scripts/eval_matrix.py` con gates, panel de jueces y modo `--offline` |

### Fase 2 â€” Latencia y streaming

| Entregable | Estado | Evidencia |
|---|---|---|
| SSE de etapas y texto validado | âœ… | `POST /chats/{sid}/messages/stream`; eventos `stage`, `token`, `done`, `error` |
| Juez fuera de la ruta crÃ­tica | âœ… | `VERIFY_MODE=async` + `BackgroundTasks`; `sync` opcional |
| `locate` condicionado | âœ… | solo `navegar` o peticiÃ³n explÃ­cita |
| Lecturas `@` en paralelo | âœ… | `ThreadPoolExecutor` con transacciÃ³n/conexiÃ³n propia |
| Deadline + degradaciÃ³n | âœ… | `services/deadline.py`, `REQUEST_DEADLINE_MS=12000` |
| CachÃ© retrieval + embeddings | âœ… | `services/cache.py` (Redis o LRU), invalidaciÃ³n al indexar |
| Circuit breaker por proveedor | âœ… | `providers/circuit.py`: 3 fallos de red/5xx â†’ cooldown 30 s |
| Bench + presupuesto | âœ… | `scripts/bench_chat.py`, `scripts/load_chat.py` |

### Fase 3 â€” Calidad avanzada

| Entregable | Estado | Evidencia |
|---|---|---|
| Query rewriting con historial | âœ… | `services/query_rewrite.py` (determinista + LLM opcional) |
| Cross-encoder local opcional | âœ… | `services/retrieval_crossencoder.py` (import perezoso + fallback) |
| ExpansiÃ³n por grafo/entidades | âœ… | `services/graph_expand.py`; `.mappings()` corregido |
| Memoria resumida por sesiÃ³n | âœ… | `services/memory.py`; `chat_sessions.summary` |
| Skills ejecutables (`tools`, `parameters`, `position`) | âœ… | `services/skills.py`, admin, migraciÃ³n 0047 |
| Panel multi-modelo de jueces | âœ… | `judge_panel()` en `eval_matrix.py` |
| Paridad MCP | âœ… (documentada) | MCP sigue siendo superficie de tools, no motor de chat |

---

## 2. Cambios de esquema

- `0047_chat_pipeline`: `chat_messages.answer_kind/intent/degraded`,
  `chat_sessions.summary/summary_upto`, `skills.tools/parameters/position`.
- `0048_chat_history_metadata`: `chat_messages.uncertainties`,
  `occurrences_term`, `occurrences_note`.
- Aplicadas y verificadas en PostgreSQL de test; `alembic upgrade head` limpio.

---

## 3. VerificaciÃ³n ejecutada

| Comando | Resultado |
|---|---|
| `pytest -m unit` | **592 passed** |
| `pytest -m integration` | **144 passed** |
| `pytest tests/integration/test_chat_pipeline.py ...` | **6 passed** |
| `pytest tests/behavior/test_chat_pagos.py` | **5 passed** |
| `python scripts/eval_offline.py` | **88/88 checks OK** |
| `python scripts/bench_chat.py` (seed) | retrieve p95 **9 ms**, dato p95 **12 ms**, navegar p95 **6 ms** |
| `npm run lint` / `npm run build` | **OK** |
| `ruff` sobre archivos modificados | **All checks passed** |

**EvaluaciÃ³n de producciÃ³n (`run_evals.py`, FakeLLM = lÃ­mite inferior mecÃ¡nico):**

- `coherence_pass = 1.000` (invariantes deterministas).
- `retrieval_recall = 0.636`; `citation_precision = 0.667`.
- `p95 = 32 ms` (sin latencia de proveedor).
- 39/50 casos evaluados; 11 omitidos por `case_ref` ausente en la base de test.

> Con `FakeLLM` las mÃ©tricas de citas/faithfulness no miden calidad del modelo:
> el fake cita siempre la primera evidencia. Las mÃ©tricas deterministas
> (coherencia, presupuesto, latencia, abstenciÃ³n) sÃ­ son vÃ¡lidas.

---

## 4. Decisiones tomadas

1. **La abstenciÃ³n sigue siendo blanda en retrieval**, pero el compositor no
   presenta como `partial` un material con cobertura < 0,6 o con un nÃºmero de la
   pregunta ausente de toda la evidencia. AsÃ­ se evita el falso â€œno hay
   evidenciaâ€ y tambiÃ©n la respuesta decorativa.
2. **El texto que se transmite por SSE es el compuesto y validado**, no los
   tokens crudos del modelo. Evita mostrar afirmaciones que luego se descartan.
3. **La navegaciÃ³n devuelve solo la nota en Markdown**; la lista agrupada vive en
   `occurrences` y la pinta la UI. Se elimina el muro de timestamps duplicado.
4. **El fallback SSE es idempotente** por texto+adjuntos+ventana de 2 minutos;
   el POST de respaldo no duplica el mensaje del usuario.
5. **El filtro de embeddings ignora placeholders** (`__SET_ME__`): en test/dev no
   vacÃ­a la rama vectorial.
6. **MCP no se convierte en un segundo motor de chat**; se documenta la paridad
   como superficie de tools.

---

## 5. Deuda preexistente (no introducida por este cambio)

- `tests/behavior/test_must_not.py::test_not_12`: el endpoint de borrado de
  documentos devuelve 200 y elimina, mientras el test exige 405
  `DOCUMENT_IMMUTABLE`. Es una decisiÃ³n de producto/seguridad anterior al commit
  auditado; no se tocÃ³ para no romper la UI de borrado.
- `ruff check apps tests scripts` reporta 40 hallazgos en mÃ³dulos ajenos
  (`asr.py`, `diarization.py`, `media_pipeline.py`, scripts de carga). Los
  archivos de este cambio pasan limpios.
- La matriz cross-model real (Gemini/DeepSeek/OpenAI/Claude/Kimi) requiere claves
  y ejecuciÃ³n con `--models`; el harness, los gates y el modo offline estÃ¡n
  listos, pero no se llamaron APIs externas en esta sesiÃ³n.

---

## 6. Estado real de las bases (2026-10-19)

Migraciones aplicadas con el rol owner de cada instancia, sobre el PostgreSQL
compartido `34.23.102.250:5432`:

| Base | Antes | DespuÃ©s | Columnas nuevas |
|---|---|---|---|
| `judicial_develop` | `0046_chat_ocr_asr_mode` | `0048_chat_history_metadata` | verificadas |
| `judicial_quality` | `0046_chat_ocr_asr_mode` | `0048_chat_history_metadata` | verificadas |
| `judicial` (producciÃ³n) | `0046_chat_ocr_asr_mode` | `0048_chat_history_metadata` | verificadas |

Datos: 1 expediente, 4.649 chunks, 53 sesiones y 125 mensajes en cada base.

### Bloqueante detectado en los Ã­ndices

Los 4.649 chunks tienen `embedding_model='__SET_ME__'`, mientras el entorno
declara `EMBEDDING_MODEL=gemini-embedding-001`. Las pruebas de similitud entre
chunks dan cosenos cercanos a cero y sin estructura semÃ¡ntica clara: los vectores
actuales **no son confiables** como embeddings del modelo declarado.

Consecuencia: el cÃ³digo nuevo, por defecto
(`RETRIEVAL_ALLOW_EMBEDDING_FALLBACK=false`), **no mezcla** esos vectores; usa
FTS con prefijos/raÃ­ces y registra `embedding_model_mismatch`. El chat sigue
respondiendo por vÃ­a lÃ©xica, pero pierde recall semÃ¡ntico hasta reindexar.

**AcciÃ³n requerida:** reindexar el expediente con el modelo real
(`scripts/index_chunks_cli.py` o `index_case`) en dev, quality y producciÃ³n, y
verificar que `embedding_model` quede en `gemini-embedding-001`. No se ejecutÃ³
porque recalcula 4.649 vectores y consume API de embeddings; requiere decisiÃ³n
explÃ­cita.

### CÃ³digo desplegado

Las migraciones son compatibles hacia atrÃ¡s (columnas nullable/default), asÃ­ que
las instancias actuales siguen funcionando. Pero **las imÃ¡genes de API/web/MCP
todavÃ­a no incluyen este cÃ³digo**: falta build, push y redeploy en las tres
instancias, mÃ¡s healthcheck y smoke test del chat.

---

## 7. CÃ³mo ejecutar la matriz real

```powershell
$env:ENV_FILE=".env.test"
python scripts/eval_matrix.py `
  --models "gemini:gemini-2.0-flash,deepseek:deepseek-chat,openai:gpt-4o-mini,anthropic:claude-3-5-sonnet,kimi:kimi-k2" `
  --judge "openai:gpt-4o-mini" `
  --golden evals/golden/chat_pipeline.yaml `
  --org-id <org> --user-id <user> `
  --out var/eval_matrix.json
```

Gates: `coherence_pass=1.0`, `citation_precision â‰¥ 0.85`, `faithfulness â‰¥ 0.90`,
`abstention_accuracy â‰¥ 0.90`, p95 de `dato` dentro del presupuesto.


---

## 8. Cierre de bloqueantes B1–B5 y 17 pendientes (2026-10-20)

### B1 — Índice de embeddings
- Diagnóstico: 4.649 chunks con `embedding_model='__SET_ME__'` y vectores sin estructura semántica.
- Gemini devolvió `402 RESOURCE_EXHAUSTED` al intentar reindexar.
- Se añadió `LocalNgramEmbedding` (`local-ngram-1024`, 1024 dimensiones, sin red):
  palabras + bigramas + n-gramas de caracteres con hashing con signo.
- Reindexados **13.787 chunks** en dev, quality y producción; `embedding_model`
  ahora es `local-ngram-1024` en las tres bases.
- La consulta detecta el modelo real del índice y usa el proveedor coherente,
  aunque el `.env` siga apuntando a Gemini. `scripts/reindex_embeddings.py --all`
  deja el cambio a Gemini en una operación.

### B2 — Despliegue
- Las migraciones 0047–0050 están aplicadas en las tres bases.
- El árbol local está listo para build/push; las imágenes del VPS deben
  reconstruirse y redesplegarse. `scripts/deploy_all.sh` ya reindexa con
  `--case-id --org-id --user-id` tras el deploy.

### B3 — Permisos de tools
- Mapa único `case_tools.required_permission()`; escritura solo con
  `ToolContext.allow_write=True`, que el loop del LLM nunca activa.
- MCP exige el permiso por tool y `confirm` explícito.
- El flujo de confirmación del chat aplica la corrección desde el servidor.
- Prompt `agent_system.v8.md`: el LLM propone, nunca aplica.

### B4 — Matriz cross-model
- El harness y el modo offline están operativos.
- Solo hay clave de Gemini; las claves de OpenAI, Anthropic, DeepSeek y Kimi no
  están en el entorno. No se inventaron ejecuciones: el gate real queda
  condicionado a esas claves.

### B5 — CI y deuda
- `test_not_12` corregido: documentos y media originales devuelven 405
  `DOCUMENT_IMMUTABLE`/`MEDIA_IMMUTABLE`; el borrado va por solicitud auditada.
- `ruff check apps tests scripts` → **All checks passed**.
- Suite completa → **1.159 passed**.
- RLS forzado en `party_roles` (migración 0050).

### Los 17 pendientes
1. Chunk overlap ~30 palabras y tope 350 palabras por testimonio.
2. Chunks estructurados con fuente primaria citable; los no citables se excluyen.
3. `ANSWER_MIN_GROUNDING_OVERLAP` calibrado a 0.34 (script `calibrate_grounding.py`).
4. `reindex_embeddings.py` + hook de deploy para reindexar al cambiar modelo.
5. `search_tsv` en español + unaccent en páginas, segmentos y chunks (0049).
6. `AGENT_TOOL_RESULT_MAX_CHARS=1200` en vez de 400.
7. `CHAT_HISTORY_MAX_CHARS=6000` con recorte desde el turno más antiguo.
8. UI: Copiar, Regenerar y Reintentar en cada respuesta.
9. `DEFAULT_AGENT_VERSION=8` + `refresh_agent_prompts.py`.
10. Cifrado Fernet de API keys/SMTP + `encrypt_secrets.py` (6 secretos por base).
11. `MODEL_NOT_FOUND` explícito en vez de fallback silencioso.
12. MCP documentado como superficie de tools (misma capa, JWT/RLS).
13. Workflow nightly `.github/workflows/nightly.yml`.
14. κ del juez = **1.0** sobre 50 etiquetas de referencia (`judge_agreement.py`).
15. Carga 10 workers/300 s: **5.138 peticiones**, p95 **237 ms**, 0 errores; TTFT stage p95 **37,7 ms**, primer token p95 **94,0 ms** (scripts/ttft_chat.py).
16. Diagramas actuales: `chat-ia-arquitectura-actual.html` y `chat-ia-turno-actual.html`.
17. Plan marcado con 23/23 checkboxes.

> Nota de honestidad: las 50 etiquetas de κ son un set de referencia construido
> desde el golden set, no anotación humana independiente. Cuando haya anotadores,
> se reemplaza `evals/judge/reference_labels.yaml` sin cambiar el runner.
