# Evidencia local — formato, coherencia, grounding y velocidad

**Fecha:** 2026-10-20  
**Entorno:** Docker local `judicial-ai` (PostgreSQL 54322, API 8000, Web 3000, MCP 8100)  
**Modelo real:** DeepSeek `deepseek-flash`  
**Embeddings:** `local-ngram-1024` (13.787 chunks)  
**Datos:** expediente `11001310302120180036100`

## 1. Stack local actualizado

- `docker compose --env-file .env.local-ai up -d --build api web mcp`
- API/Web/MCP: **healthy**.
- Migraciones locales aplicadas: `0050_party_roles_rls`.
- Base local reindexada: `embedding_model='local-ngram-1024'`, **13.787 chunks**.

## 2. B3 — permisos y escritura

Comando:

```powershell
pytest tests/unit/test_secrets.py tests/unit/test_case_tools.py `
  tests/integration/test_chat_tools.py tests/integration/test_mcp_server.py -q
```

Resultado: **42 passed**.

Cubre:

- mapa único `tool → permiso`;
- escritura bloqueada sin `allow_write` del servidor;
- MCP exige permiso por tool;
- preview sin `confirm` no muta;
- `confirm` desde el LLM no puede aplicar la escritura.

## 3. B4 — proveedores reales (solo local)

Estado de claves y modelos:

| Proveedor | Estado | Modelos |
|---|---|---|
| DeepSeek | **200 OK** | `deepseek-flash`, `deepseek-v4-pro` |
| Kimi | **200 OK** al listar; luego **429 Too Many Requests** | `kimi-for-coding`, `kimi-for-coding-highspeed`, `k3`, `k3-256k` |
| OpenAI | **401 Unauthorized** con la clave entregada | — |
| Gemini | sin clave local; el proveedor remoto devolvió 402 | — |

Matriz real ejecutada con DeepSeek (10 casos, juez DeepSeek):

- `coherence_pass = 1.000`
- `forbidden_hit = 0`
- `answer_kind_match = 0.900`
- `faithfulness = 0.700`
- `citation_correctness = 0.720`
- `naturalness = 0.800`
- `abstention = 0.800`
- `judge_errors = 0`

La matriz Kimi se intentó con 10 casos; los casos `navegar` respondieron, los `dato` quedaron vacíos y el proveedor terminó devolviendo **429**. OpenAI no autenticó. No se inventaron resultados.

## 4. Formato y coherencia observados

| Pregunta | Intención | Formato | Citas | Ubicaciones |
|---|---|---|---|---|
| ¿Qué evidencia hay sobre pagos? | `dato` | `direct` | 3 | **0** |
| ¿En qué minuto se habla de pagos? | `navegar` | `navigational` | 0 | 8 agrupadas |
| ¿Quién firmó el contrato? | `dato` | `insufficient` | 0 | 0 |
| ¿Cuándo se firmó el contrato? | `dato` | `direct` | 2 | 0 |
| ¿Qué documentos conforman el expediente? | `dato` | `insufficient` | 0 | 0 |

Invariantes:

- `insufficient` ⇒ sin claims, sin citas y sin ocurrencias.
- `navegar` ⇒ sin claims; lista de menciones etiquetada.
- Citas visibles ≤ 4; sin duplicados por página/segmento.
- Respuesta en prosa, no concatenación de claims.
- Se distingue alegación de parte, testimonio y documento.

## 5. Velocidad (DeepSeek real, 10 casos)

`run_evals.py --llm provider`:

| Métrica | Valor |
|---|---|
| p50 | **4,61 s** |
| p95 | **6,43 s** |
| retrieval | ~0,3–0,5 s |
| síntesis | el resto |
| presupuesto `dato` | 8,00 s |

El ajuste decisivo fue cargar `answer_question.v2.md` (antes se cargaba v1) y limitar a 4 claims/~120 palabras. Con `LLM_MAX_TOKENS=1500` el p95 bajó de **12,15 s a 6,43 s**.

## 6. B5 — CI y deuda

- CI GitHub: run **39426392710 → success**.
- Nightly: run **39427015308 → success**.
- Suite completa local: **1.159 passed**.
- `ruff check apps tests scripts`: **All checks passed**.
- `test_not_12`: 405 `DOCUMENT_IMMUTABLE`/`MEDIA_IMMUTABLE`.
- RLS forzado en `party_roles`.

## 7. Límites que quedan visibles

- La métrica dorada de `citation_precision` por página exacta sigue baja (0,083): el modelo cita páginas correctas del mismo documento pero no siempre la página que el golden marca como esperada. La corrección semántica del juez es 0,72.
- Kimi agotó cuota (429) y OpenAI devolvió 401; no hay matriz completa de 4–5 proveedores.
- Gemini no tiene crédito de embeddings/LLM en la cuenta actual.
- El despliegue remoto no se tocó: la evidencia es **local**, como se pidió.
