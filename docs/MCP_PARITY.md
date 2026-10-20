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
