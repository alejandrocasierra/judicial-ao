# Línea de tiempo procesal y Process Graph

Documento de referencia del sprint que completó la arquitectura
`Expediente → documentos → extracción → eventos → normalización → línea de tiempo → grafo → agente`.

## Capas

| Capa | Dónde | Qué hace |
|---|---|---|
| Extracción | `services/legal_extraction.py` | OCR/ASR → actuaciones (`events`, `kind='procedural'`) con citas trazables. |
| Segmentación | `services/document_segmentation.py` | Etiqueta cada página: `encabezado`/`cuerpo`/`decision`/`anexos` antes de extraer. |
| Catálogo | `domain/procedural_catalog.py` | Normaliza `subtype` a un repertorio canónico (`auto_admite_demanda` → `auto_admisorio`). |
| Consolidación | `services/event_consolidation.py` | Fusiona actuaciones reales duplicadas (misma fecha/subtipo/texto) y une sus citas. |
| Relaciones | `services/procedural_graph.py` | `link_events` (refers_to/precede/appeals/responds_to) + `review_events` (marcas). |
| Grafo | `services/graph.py` | Proyecta `event_relationships` a `graph_edges` (`PRECEDES`/`CAUSES`/`APPEALS`/`RESPONDS_TO`/`REFERS_TO`). |
| Agente | `packages/prompts/agent_system.v7.md` | Para preguntas procesales usa primero `process_path` y `event_relations`. |
| Frontend | `dashboard/procesos/page.tsx` + componentes | Cuatro vistas: cronológica, por instancia, grafo causal y auditoría. |

## Fechas

`events` distingue:

- `event_date` — fecha del acto (`date_type = actuacion`).
- `document_date` — fecha del documento que lo contiene.
- `incorporation_date` — fecha en que el documento/medio se incorporó al expediente.

La línea de tiempo ordena por `COALESCE(event_date, document_date, incorporation_date)`.
Las fechas placeholder (`1899-12-31`, `1900-01-01`, `0001-01-01`) se descartan en `_safe_date`.

## Vistas del frontend

- **Cronológica**: una sola secuencia ordenada por fecha efectiva.
- **Por instancia**: acordeón por Primera/Segunda/Casación/Tutela/Ejecución…
- **Grafo causal**: columnas por instancia, tiempo hacia abajo; flechas = relaciones del Process Graph.
- **Auditoría**: documento/página de origen de cada actuación, actuaciones sin fecha y marcas de revisión.

## Regeneración automática

La secuencia `file_ingest → legal_extraction → procedural_links → graph_build` reconstruye
todo (consolidación de eventos/entidades, relaciones, grafo) tras subir, corregir o borrar
documentos y videos. El botón «Analizar vínculos» ejecuta lo mismo en primer plano.

## Event Store separado

Ver `docs/adr/0001-event-store.md` (aplazado a un sprint dedicado).
