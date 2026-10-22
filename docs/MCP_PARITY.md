# Paridad MCP — decisión de arquitectura

**Decisión (cerrada):** MCP es una **superficie de tools y recursos**, no un
segundo motor de chat. Expone la misma capa `case_tools`, el mismo JWT, RLS por
organización y el mismo mapa `tool → permiso`. No ejecuta router de intención,
compositor, memoria ni SSE.

## Qué comparte con el chat

- `case_tools.execute` y `TOOL_REGISTRY`.
- `required_permission()`: lectura `ai.query`; escritura `document.upload` /
  `media.upload` según la tool.
- `ToolContext`: org, actor, locale, modos OCR/ASR y `post_commit`.
- Auditoría, reviews, propagación a pgvector/grafo.
- Confirmación de escritura en dos pasos.

## Qué no comparte (por diseño)

- Router de intención y presupuesto de citas.
- Compositor determinista y `answer_kind`.
- SSE y memoria resumida.
- Juez semántico post-respuesta.

## Consecuencia operativa

Un cliente MCP (Claude Code, Cursor) recibe **evidencia estructurada** y decide
su propia presentación. Si se necesita el formato del chat, se usa el endpoint
HTTP `/v1/cases/{id}/chats/{sid}/messages/stream`, no MCP. Esta separación evita
dos compositores divergentes y mantiene una sola política de citas.

---

## Tools de los modulos legales (2026-10-22)

Se agregaron a `case_tools` y al servidor MCP, siempre como lectura:

- `get_fact`
- `list_facts`
- `verify_citation`
- `lookup_legal_rule`

`verify_citation` y `lookup_legal_rule` no sustituyen al gate deterministico:
solo devuelven la fuente/regla para que el agente razone. La segunda opinion y
la extraccion de hechos tienen allowlists de solo lectura; no pueden escribir
`facts`, `claims`, `evidence`, `documents` ni `legal_rules`.
