# Arquitectura de los módulos legales

Artefactos Archify generados y validados el **2026-10-22** sobre la implementación
real de `judicial-ai` (estado local, sin commit ni despliegue).

| # | Diagrama | Pregunta que responde |
|---|---|---|
| 00 | [Contexto del sistema](00-system-context.html) | ¿Dónde vive cada módulo y qué habla con qué? |
| 01 | [Gate legal](01-legal-guard-flow.html) | ¿Cómo se decide publicar una respuesta? |
| 02 | [Segunda opinión](02-second-opinion-sequence.html) | ¿Quién ve qué y en qué orden? |
| 03 | [Ciclo de vida de hechos](03-facts-lifecycle.html) | ¿Cómo pasa un hecho de alegación a probado? |
| 04 | [Modelo de datos](04-data-model.html) | ¿Cómo se relacionan las tablas nuevas y las anclas? |
| 05 | [Navegación y UX](05-ux-navigation.html) | ¿Cómo se navega sin convertir el producto en tres silos? |

Cada HTML es autónomo: SVG inline, tema claro/oscuro, sin CDN ni red. El JSON
fuente queda junto al HTML y es lo que se regenera; no se edita el HTML a mano.

## Evidencia de validación

Los seis diagramas ejecutaron el flujo completo de Archify **3.0.1**
(`validate` → `deliver` → `check` → `browser-check`) mediante `finalize
--quality showcase`. Los recibos quedan versionados junto a cada artefacto:

| Diagrama | Receipt de finalize | Browser check | Artefacto (bytes) |
|---|---|---|---|
| 00 | `00-system-context.finalize-summary.json` | `00-system-context.browser-check.json` | 762.196 |
| 01 | `01-legal-guard-flow.finalize-summary.json` | `01-legal-guard-flow.browser-check.json` | 761.630 |
| 02 | `02-second-opinion-sequence.finalize-summary.json` | `02-second-opinion-sequence.browser-check.json` | 757.989 |
| 03 | `03-facts-lifecycle.finalize-summary.json` | `03-facts-lifecycle.browser-check.json` | 760.764 |
| 04 | `04-data-model.finalize-summary.json` | `04-data-model.browser-check.json` | 793.056 |
| 05 | `05-ux-navigation.finalize-summary.json` | `05-ux-navigation.browser-check.json` | 768.420 |

- `04-data-model` se publicó dos veces: la evidencia del primer intento quedó en
  `04-data-model.review-1/` y la del segundo (versión final, sin cruces) en
  `04-data-model.review-2/`. El HTML y los recibos de la raíz corresponden a la
  versión final.
- `05-ux-navigation` se regeneró cuando P1 integró `AuditBadge` en el chat y
  `SecondOpinionDiff` en el panel: las versiones previas quedaron en
  `05-ux-navigation.review-1/` y `review-2/`. El HTML y los recibos de la raíz
  corresponden a la versión final (`browser-check` y `visual-check` pasan).
- `browser-check` mide 1440×900, 1600×1000, 1920×1080 y 2048×1320, tema claro y
  oscuro, estados READ/Still y desborde horizontal. No produce capturas.
- **Revisión perceptual:** se capturaron e inspeccionaron `04-data-model` y
  `05-ux-navigation` a 1440×900 en tema claro (`*.visual-check/`): sin etiquetas
  cortadas ni cruces visibles. No se revisaron 00-03, el tema oscuro ni 2048 px.
  El resto queda como compuertas automáticas, no como aprobación visual.

### Cómo se regeneró

```bash
# Desde la raíz del repo; <archify> es la ruta de la skill instalada.
node <archify>/bin/archify.mjs finalize architecture docs/architecture/04-data-model.json docs/architecture/04-data-model.html --quality showcase --json
node <archify>/bin/archify.mjs finalize workflow docs/architecture/05-ux-navigation.json docs/architecture/05-ux-navigation.html --quality showcase --json

# Diagnóstico puntual, sin publicar:
node <archify>/bin/archify.mjs validate architecture docs/architecture/04-data-model.json --json --quality showcase
node <archify>/bin/archify.mjs check docs/architecture/04-data-model.html --require-provenance
node <archify>/bin/archify.mjs browser-check docs/architecture/04-data-model.html --json --require-provenance
```

Si el HTML ya tiene evidencia de otra versión, usar `--out-dir <carpeta>` para no
pisarla; el publicador de Archify falla cerrado en vez de sobrescribir evidencia
ajena.

## Decisiones de representación

- **04** es un mapa de entidades con cardinalidades, no un ERD físico: la versión
  instalada de Archify (3.0.1) no incluye el modo `erd`. Se dibujan claves y
  estados, no todas las columnas; las columnas secundarias están en las tarjetas
  y en las migraciones.
- **04** cubre las migraciones `0055` a `0061`. La `0062` (gate semántico, precios
  de modelo y evidencia de `EVIDENCE_SUPPORTED`) es de P1 y todavía no está
  reflejada; al cerrarse hay que regenerar el diagrama.
- **05** refleja la UI implementada al 2026-10-22: sidebar plana, tres pestañas
  (`Expediente`, `Hechos`, `Auditoría`), `AuditBadge` por respuesta en el chat,
  panel de auditoría y diff de segunda opinión en un diálogo. Lo que falta
  (layout compartido, sidebar agrupada, pestaña de Cronología) está en la tarjeta
  "Pendiente de UI". P1 sigue integrando componentes: si cambian, regenerar `05`.
- Ningún diagrama usa nombres, radicados ni cifras de expedientes reales. Las
  cifras de corpus y derivados viven en la auditoría, no en los diagramas.

## Fuente de verdad

- Código y migraciones `0055` a `0061` (la `0062` está en curso por P1).
- Plan: [`../PLAN_MODULOS_LEGALES_2026-10-22.md`](../PLAN_MODULOS_LEGALES_2026-10-22.md)
- Contratos: [`../CONTRATOS_HARNESS_MODULOS_LEGALES.md`](../CONTRATOS_HARNESS_MODULOS_LEGALES.md)
- Diseño: [`../GUIA_DISENO_MODULOS_LEGALES.md`](../GUIA_DISENO_MODULOS_LEGALES.md)
- Implementación: [`../IMPLEMENTACION_MODULOS_LEGALES_2026-10-22.md`](../IMPLEMENTACION_MODULOS_LEGALES_2026-10-22.md)
- Auditoría de gaps: [`../AUDITORIA_MODULOS_LEGALES_2026-10-22.md`](../AUDITORIA_MODULOS_LEGALES_2026-10-22.md)
- Plan de diagramas: [`../ARCHIFY_MODULOS_LEGALES.md`](../ARCHIFY_MODULOS_LEGALES.md)
