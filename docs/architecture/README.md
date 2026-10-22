# Arquitectura de los módulos legales

Artefactos Archify generados y validados el 2026-10-22 sobre la implementación
real de `judicial-ai`.

| Diagrama | Qué explica |
|---|---|
| [Contexto del sistema](00-system-context.html) | Web, API, workers, PostgreSQL/pgvector, grafo, MCP y modelos. |
| [Gate legal](01-legal-guard-flow.html) | De la pregunta a `PASSED`, `WARNING` o `BLOCKED`; validación determinista y juez. |
| [Segunda opinión](02-second-opinion-sequence.html) | Fase 1 ciega, comparación y persistencia asíncrona. |
| [Ciclo de vida de hechos](03-facts-lifecycle.html) | De alegación a admitido/probado; revisión y estados terminales. |

## Evidencia

- Los cuatro diagramas pasaron `validate`, `deliver`, `check` y `browser-check` de Archify.
- Los JSON fuente están junto a cada HTML.
- Los recibos `.finalize.json` y `.browser-check.json` quedan versionados.
- No se usaron nombres, radicados ni cifras de expedientes reales en los diagramas.

## Fuente de verdad

- Plan: [`../PLAN_MODULOS_LEGALES_2026-10-22.md`](../PLAN_MODULOS_LEGALES_2026-10-22.md)
- Contratos: [`../CONTRATOS_HARNESS_MODULOS_LEGALES.md`](../CONTRATOS_HARNESS_MODULOS_LEGALES.md)
- Diseño: [`../GUIA_DISENO_MODULOS_LEGALES.md`](../GUIA_DISENO_MODULOS_LEGALES.md)
- Implementación: [`../IMPLEMENTACION_MODULOS_LEGALES_2026-10-22.md`](../IMPLEMENTACION_MODULOS_LEGALES_2026-10-22.md)
- Código y migraciones `0055` a `0061`.
