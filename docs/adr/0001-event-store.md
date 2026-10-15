# ADR 0001 — Event Store separado (`case_events` / `normalized_events`)

- Estado: **Aplazado** (decisión tomada al cerrar el sprint de línea de tiempo + Process Graph).
- Fecha: 2026-10.

## Contexto

La arquitectura objetivo del producto es:

```
Expediente → documentos → extracción → eventos procesales → normalización →
línea de tiempo → grafo/caso → agente jurídico
```

Hoy el "event store" es la tabla `events` (con `kind = 'procedural'` para actuaciones
y `kind = 'generic'` para eventos), y `event_relationships` construye el Process Graph
sobre ella. Las citas (`citations`) aportan la evidencia documento+página o media+timestamp.

Se propuso separar el almacén en:

- `case_events`: event store **inmutable**, una fila por evento extraído de un documento.
- `normalized_events`: vista **canónica deduplicada**.
- `event_relationships`: construido sobre `normalized_events`.

## Decisión

**Aplazar** el refactor del Event Store separado a un sprint dedicado. No se implementa
en este sprint.

## Motivos

1. **Alto alcance**: exige migrar `events` → `case_events` + `normalized_events`, reapuntar
   `event_relationships`, y actualizar extracción (`legal_extraction`), grafo (`graph.py`),
   agente (`case_tools`), frontend, exportaciones y tests.
2. **Bloquea el resto**: cualquier trabajo sobre eventos quedaría congelado hasta terminar.
3. **Riesgo/beneficio**: los cambios de este sprint (proyección al grafo, fechas separadas,
   consolidación de eventos, segmentación documental, catálogo de subtipos, uso del Process
   Graph por el agente, vistas de línea de tiempo y auditoría) ya entregan el valor visible.
4. **Entorno**: sin acceso a la base de datos ni a la suite de integración, un refactor
   grande aumenta el riesgo de regresiones silenciosas.

## Consecuencias

- Se mantiene `events` como almacén único, con deduplicación **in situ**
  (`event_consolidation.consolidate_case_events` fusiona actuaciones reales duplicadas) y
  resolución de referenciados (`procedural_graph.link_events`).
- La separación inmutable/canónica queda pendiente; el linaje ya se conserva vía `citations`
  y `subtype_raw`.

## Plan futuro (sprint dedicado)

1. Crear `case_events` (inmutable; una fila por extracción) con `document_id`, `page_number`,
   texto fuente y hash de entrada.
2. Crear `normalized_events` (canónica) con un array `source_event_ids` que apunte a sus
   orígenes.
3. Migrar `event_relationships` para que apunte a `normalized_events`.
4. Actualizar `procedural_graph.py`, `graph.py`, `legal_extraction.py`, `case_tools/read.py`,
   `export.py` y `routers/cases.py`.
5. E2E de regeneración completa de un caso + pruebas de integración.
