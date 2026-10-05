# Plan — Chat IA inteligente + MCP (judicial-ai)

> Estado: FASE 0 ✅ · FASE 1 ✅ · FASE 2 ✅ (servidor MCP real) · FASE 3 ✅ (chat multi-turn) · FASE 4 ✅ (frontend) · FASE 5 ✅ (skills/agentes sembrados) · FASE 6 ✅ (corrección conversacional: detección determinista de intención, propuesta pendiente en servidor, confirmación fiable sin depender del LLM, propagación BD+reviews+pgvector+grafo, suggest_reprocess). PLAN COMPLETO.
> Decisiones: (1) Servidor MCP real (FastMCP) + tools internas compartidas · (2) Entrega por fases verificables · (3) Persistencia de chat multi-turn con `chat_sessions`/`chat_messages`.
> Lección F1: la propagación de las tools de escritura (reindex/encolar) se difiere a `ctx.post_commit` y se drena tras confirmar la transacción del llamador — hacerlo en otra conexión con la tx abierta produce auto-deadlock (row locks + advisory lock de audit_chain).
> Extra F6/URLs: cada modelo del dashboard admite `api_base_url` (override del endpoint: proxy, otra región o VPS). El adaptador calcula el path correcto por base (OpenAI `/v1/chat/completions`, Gemini `…/v1beta/openai/chat/completions`, Anthropic `/v1/messages`), así el chat funciona con CUALQUIER proveedor/API key. Para migrar a una VPS solo se cambian las URLs en `.env` (o por modelo) sin tocar código; el SDK MCP va en `requirements/mcp.txt` (capa Docker aparte).

## Principios (heredados de CLAUDE.md)

- **Encapsulado**: el chat SOLO responde con datos del expediente (BD, OCR, ASR, pgvector, grafo). Nunca busca en internet. Reforzado en system prompts y en la descripción de tools.
- **Evidencia-first**: toda afirmación lleva cita (documento+página o media+timestamp). Las reglas de grounding actuales (`parse_and_validate`, `verification.verify`) se mantienen.
- **RLS multi-tenant**: toda tabla nueva con `organization_id`, RLS FORCE, `enforce_same_org`, grants al rol de la app.
- **Correcciones humanas**: van a `reviews` (salida original de la IA preservada), auditadas, y propagan a BD → pgvector → grafo.
- **i18n es/en**: toda clave nueva en ambos catálogos.
- **Pruebas primero**: cada fase cierra con su suite en verde.

---

## FASE 0 — Fundaciones de datos (migración)

**Objetivo**: persistencia de conversaciones y adjuntos.

### Tablas nuevas (migración Alembic + `db/sql/025_chat_tables.sql`)

**`chat_sessions`**
| Columna | Tipo | Notas |
|---|---|---|
| id | uuid PK | |
| organization_id | uuid FK | RLS |
| case_id | uuid FK cases | una sesión pertenece a un expediente |
| title | text | auto-generado de la 1ª pregunta |
| agent_id | uuid FK agents NULL | agente elegido con `/` |
| model_id | uuid FK ai_models NULL | modelo elegido en el selector |
| created_by / created_at / updated_at | | |

**`chat_messages`**
| Columna | Tipo | Notas |
|---|---|---|
| id | uuid PK | |
| organization_id | uuid FK | RLS |
| session_id | uuid FK chat_sessions | |
| role | text CHECK ('user','assistant','tool') | |
| content | text | |
| attachments | jsonb | `[{kind:'document'|'media', id, name}]` — los `@` reales |
| citations | jsonb | citas persistidas de la respuesta |
| model_run_id | uuid FK model_runs NULL | trazabilidad con el pipeline existente |
| created_at | timestamptz | |

- RLS FORCE + policy por `current_org()`, trigger `enforce_same_org` (FK session→chat_sessions), grants `DB_APP_USER`.
- Índices: `(session_id, created_at)`, `(case_id)`.
- **Tests**: `test_sec_ten_16` debe detectar las tablas; prueba de integración de aislamiento entre orgs.

**Entregable**: migración aplicada, tablas bajo RLS, tests de tenancy en verde.

---

## FASE 1 — Capa de tools compartida (`app/services/case_tools/`)

**Objetivo**: una sola implementación de cada herramienta, consumida por (a) el loop del agente interno y (b) el servidor MCP. Cada tool declara: nombre, descripción (es/en), JSON Schema de argumentos, permiso RBAC requerido, y contrato de resultado con citas.

### Tools de lectura

| Tool | Qué hace | Fuente |
|---|---|---|
| `search_case` | Búsqueda híbrida FTS + pgvector + RRF en todo el expediente. Acepta `document_ids`/`media_ids` para acotar (los `@`). | `answering.retrieve` refactorizada |
| `get_document_page` | Texto OCR de una página (modo `basico`/`document_ai`), con confianza y `needs_review`. | existe en agent_tools |
| `read_document` | Documento completo o rango de páginas (para "¿qué dice el documento @001?"). | nuevo sobre `document_pages` |
| `search_transcripts` | FTS sobre segmentos ASR. | existe — corregir config FTS del caso |
| `search_transcript_by_time` | **Minuto exacto**: "¿en qué minuto se habló de X?" → segmentos con `start_ms/end_ms` + hablante (nombre resuelto). Soporta rango: "qué se dijo entre el min 10 y 15". | nuevo sobre `transcript_segments` + `speakers` |
| `get_video_segment` | Segmento(s) alrededor de un timestamp. | existe |
| `list_case_files` | Lista documentos y media del expediente (resolver `@001` → id). | nuevo |
| `get_file` | "Tráeme el PDF" → metadatos + URL de descarga/visualización + nº páginas/duración + modos OCR disponibles. | nuevo sobre storage |
| `graph_query` | Consulta al grafo (personas, claims, hechos, eventos, decisiones). | existe |
| `graph_neighbors` | Vecinos de un nodo (traverse depth=1..2). | nuevo sobre `graph.traverse` |
| `find_person` | "¿Quién es X?" → nodo Person + sus claims/testimonios/eventos con citas. | nuevo (912 nodos Person) |
| `get_timeline` | Eventos ordenados por fecha con citas. | existe (search_timeline) |

### Tools de escritura (corrección humana — siempre con confirmación)

| Tool | Qué hace | Propagación |
|---|---|---|
| `correct_ocr_page` | Corrige texto OCR de una página. | `document_pages` (+`human_corrected`) → inserta en `reviews` → re-indexa pgvector → encola rebuild del grafo → aprende términos en lexicón |
| `correct_transcript_segment` | Corrige texto de un segmento ASR. | `transcript_segments` → `reviews` → re-indexa pgvector → grafo |
| `suggest_reprocess` | "¿Cómo mejoramos el OCR?" → propone reprocesar con el otro modo (basico↔document_ai) o ASR; encola el job si el usuario acepta. | workers existentes |

> Regla: las tools de escritura NUNCA se ejecutan en el primer turno. El agente propone la corrección ("voy a cambiar la página 12 de X a: …"), el usuario confirma, y recién ahí se ejecuta. Bloqueo optimista + `reviews` + audit (regla 8 de CLAUDE.md).

### Cambios en el loop interno

- `agent_tools.py` pasa a importar de `case_tools/` (misma implementación, se elimina duplicación).
- `agent.py`: el system prompt del agente = `agent.system_prompt` **+ system_prompt de cada skill enlazada** (hoy las skills son data muerta — este es el fix central).
- `QueryIn` extendido: `session_id: UUID | None`, `attachments: list[Attachment]`, `Attachment{kind, id}`.

**Tests**: unit por tool (args inválidos, scoping por caso), integration con PostgreSQL real, seguridad (tool con id de otra org → vacío/403), behavior (minuto exacto devuelve ms correctos).

**Entregable**: 15 tools funcionando desde el loop interno; `@` con IDs reales ya responde acotado al archivo adjunto; skills vivas en el prompt.

---

## FASE 2 — Servidor MCP real (`apps/mcp_server/`)

**Objetivo**: exponer las tools de la Fase 1 como MCP estándar (usable desde Claude Code, Cursor, etc.), sin duplicar lógica.

- **Stack**: FastMCP (SDK oficial `mcp`), transporte **streamable HTTP** (`/mcp`), despliegue como servicio aparte en `docker-compose.yml` y opción `uvicorn` local.
- **Auth**: mismo JWT de la plataforma (header `Authorization: Bearer`); el servidor resuelve `org_id`/`user_id` y abre `tx(org, actor)` por llamada → RLS garantiza aislamiento.
- **Tool allowlist por rol**: lectura (`ai.query`), escritura (`document.correct` / permiso de review). Se reusa `config/rbac.yaml`.
- **Recursos MCP** además de tools: `case://{id}/files`, `case://{id}/graph/stats`, `doc://{id}/page/{n}` (lectura directa).
- Config por entorno (`MCP_SERVER_PORT`, `MCP_SERVER_ENABLED` — sin defaults, en `.env.example` y `Settings`).
- **Tests**: integration con cliente MCP in-proceso; security (token de otra org, tool sin permiso).

**Entregable**: `docker compose up mcp` sirviendo las 15 tools; verificado con un cliente MCP real.

---

## FASE 3 — Backend de chat multi-turn

**Objetivo**: memoria de conversación + adjuntos + sesiones.

### Endpoints nuevos (`routers/chats.py`, prefix `/cases/{case_id}`)

| Endpoint | Descripción |
|---|---|
| `GET /chats` | Lista sesiones del expediente (para `/dashboard/chats`) |
| `POST /chats` | Crea sesión (agent_id, model_id opcionales) |
| `GET /chats/{sid}/messages` | Historial paginado |
| `POST /chats/{sid}/messages` | Envía mensaje → corre el loop agente con **historial** (últimos N turnos, tope de tokens) + attachments → persiste user+assistant+citations → devuelve respuesta con `citations` y `file_cards` |
| `DELETE /chats/{sid}` | Archiva sesión |

- **Historial al LLM**: el loop ReAct recibe los últimos K turnos (config `CHAT_HISTORY_TURNS`, default 8) para follow-ups tipo "¿y quién más estaba?".
- **Adjuntos**: `attachments` acota/boostea el retrieval (`search_case` con filtro por ids) y se referencia en el prompt ("el usuario adjuntó @001 CuadernoPrincipal…").
- **File cards**: cuando el agente usa `get_file`, la respuesta incluye `file_cards:[{kind,id,name,download_url,pages,duration}]` para que la UI renderice botones Ver/Descargar.
- `model_runs` se enlaza vía `chat_messages.model_run_id` (la respuesta ya no se pierde).
- Presupuesto de tokens del caso (`BUDGET_EXCEEDED`) y rate limits se mantienen por mensaje.
- **Tests**: integration (multi-turn recuerda contexto), security (sesión de otra org), behavior (adjunto acota retrieval).

**Entregable**: chat con memoria, adjuntos y tarjetas de archivo desde API.

---

## FASE 4 — Frontend: Chat IA (`apps/web`)

> Antes de escribir código: leer `node_modules/next/dist/docs/` (Next 16.3.7 tiene breaking changes — regla de AGENTS.md).

### 4.1 `chat-widget.tsx` (rediseño)

- **`@` real**: al elegir archivo del dropdown se crea un **chip** removible sobre el input (ya no texto plano); el mensaje envía `attachments:[{kind,id}]`.
- **Chips de contexto**: archivos adjuntos, agente activo (`/`), modelo activo — todos removibles.
- **Markdown** en respuestas (negritas, listas) — renderer ligero sin dependencia pesada o `react-markdown`.
- **Citas clicables**: `doc p.12` → abre `document-ocr-viewer` en esa página; `video 14:32` → abre `media-transcript-viewer` **en ese segundo** (deep-link `?t=872`).
- **File cards**: respuesta de "tráeme el PDF" renderiza tarjeta con nombre, tamaño, botones **Ver** (inline) y **Descargar** (`/download`), miniatura de página 1 para PDFs.
- **Respuestas con minutos**: "se habló de la caución en el **minuto 14:32**, lo dijo **Juan Pérez**" → click salta el video.
- **Corrección inline**: sobre una cita OCR/ASR, botón "Corregir" → textarea con el texto actual → envía la corrección → el chat confirma "✅ Corregido en BD, pgvector y grafo".
- **Sesiones**: selector de conversación + "nueva conversación"; historial persistente al recargar.
- **Estados**: typing indicator con pasos del agente ("buscando en el grafo…", "leyendo página 12…") si el backend emite progreso (SSE opcional; si no, spinner simple).
- Selector de expediente (hoy toma `cases[0]` — debe respetar el proceso que el usuario está viendo en `/dashboard/procesos/[caseId]`).

### 4.2 Página `/dashboard/chats`

- Lista real de sesiones (expediente, título, fecha, nº mensajes), entrar a una sesión y continuarla.

### 4.3 Páginas `/dashboard/skills` y `/dashboard/agents`

- Skills: badge "Sistema" para las sembradas, contador de agentes que la usan.
- Agentes: mostrar skills por nombre (ya), indicador de cuántas tools habilita.
- (Las páginas CRUD ya existen; es mejora de UX, no rewrite.)

**Entregable**: la experiencia completa descrita — `@`, minutos de video, traer archivo, corregir desde el chat, memoria.

---

## FASE 5 — Skills y agentes sembrados (estilo grill-me / mwt-one-harness)

**Objetivo**: poblar `/dashboard/skills` y `/dashboard/agents` con habilidades jurídicas reales, al estilo del catálogo `mwt-one-harness` (propósito, tools permitidas, flujos, anti-patrones).

`builtin_agents.py` se extiende (`ensure_chat_skills`) con idempotencia:

### Skills del chat

| Skill | Propósito | Tools clave |
|---|---|---|
| **grill-me (interrogatorio)** | Modo abogado contrario: una pregunta a la vez, persigue contradicciones y lagunas probatorias del expediente hasta ~95% de claridad. NO inventa hechos; cada pregunta cita evidencia. | `search_case`, `graph_query`, `find_person` |
| **analista-documento** | "¿Qué dice @001?" — resume, extrae partes/pretensiones/pruebas de un documento adjunto, página por página si se pide. | `read_document`, `get_document_page` |
| **analista-video** | Minutaje: "¿en qué minuto se habló de X?", "¿quién lo dijo?", transcripción por rango. Siempre responde con `mm:ss` + hablante + cita. | `search_transcript_by_time`, `get_video_segment` |
| **corrector-evidencia** | Detecta intención de corrección ("está malo, es así…"), propone el cambio, pide confirmación, ejecuta y reporta propagación (BD/pgvector/grafo). Sugiere reprocesar si el OCR es de baja calidad. | `correct_ocr_page`, `correct_transcript_segment`, `suggest_reprocess` |
| **cronologista** | Línea de tiempo del proceso con citas por evento. | `get_timeline`, `graph_query` |
| **cazador-contradicciones** | Cruza claims SUPPORTS/REFUTES del grafo; toda contradicción exige revisión humana (regla 7). | `graph_query`, `search_case` |
| **relacionador-personas** | Quién es quién: 912 personas, sus dichos, decisiones y eventos. | `find_person`, `graph_neighbors` |
| **encapsulamiento** (siempre activa) | "Solo respondes con información de los expedientes del sistema. Nunca busques en internet ni uses conocimiento externo. Si no está en el expediente, dilo." | — |

### Agentes sembrados

| Agente | Skills enlazadas |
|---|---|
| **Asistente del expediente** (default) | analista-documento, analista-video, relacionador-personas, corrector-evidencia, encapsulamiento |
| **Grill-me jurídico** | grill-me, cazador-contradicciones, encapsulamiento |
| **Cronista probatorio** | cronologista, cazador-contradicciones, encapsulamiento |

**Entregable**: al abrir `/dashboard/skills` y `/dashboard/agents` ya están creados y operativos con `/` en el chat.

---

## FASE 6 — Corrección conversacional end-to-end + calidad

- Flujo completo: usuario muestra OCR/ASR → dice "está malo, es así: …" → agente propone diff → confirma → `reviews` + propagación → respuesta con verificación ("la página 12 ahora dice X; re-indexado y grafo actualizado").
- "¿Cómo podemos mejorar este OCR?" → el agente revisa `ocr_confidence`/`needs_review` y los dos modos, y propone: corrección manual de N páginas, o reprocesar con el otro motor (botón de acción).
- Hardening: rate limit de correcciones, audit completo, tests de behavior del flujo conversacional.
- Documentación: actualizar `CLAUDE.md` (mapa de código), `docs/BACKLOG.md`, y README del MCP.

---

## Orden y dependencias

```
F0 (tablas chat) ─┐
                  ├─► F1 (case_tools) ─► F2 (MCP server)
                  └─► F3 (chat multi-turn) ─► F4 (frontend) ─► F6 (corrección conv.)
                             └─► F5 (skills sembradas) ─┘
```

Cada fase cierra con `scripts/run_tests.sh` en verde + lint. F1+F3+F4 son el núcleo visible; F2 y F6 pueden ir en paralelo una vez F1 existe.
