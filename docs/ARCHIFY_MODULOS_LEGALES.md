# Plan de diagramas y documentación Archify

**Versión:** 1.0  
**Fecha:** 2026-10-22  
**Base:** commit `963297c`  
**Plan maestro:** [PLAN_MODULOS_LEGALES_2026-10-22.md](PLAN_MODULOS_LEGALES_2026-10-22.md)

## 0. Estado actual

No existe hoy una carpeta `docs/architecture/` ni artefactos Archify versionados en el repositorio. La arquitectura vive dispersa en `docs/SSD_Judicial_AI_Platform.md`, `docs/CHAT_PIPELINE_CONTRACT.md`, `docs/MCP_PARITY.md` y el código. Este plan crea la capa visual que faltaba, sin reescribir el SSD.

La fuente de verdad sigue siendo el código y las migraciones. Los diagramas explican, no reemplazan.

---

## 1. Archivos que se crean

```text
docs/architecture/
├── README.md
├── 00-system-context.html
├── 01-legal-guard-flow.html
├── 02-second-opinion-sequence.html
├── 03-facts-lifecycle.html
├── 04-data-model.html
├── 05-ux-navigation.html
└── assets/
    ├── theme.css
    └── diagram.js
```

Cada HTML es autónomo, con SVG inline, tema claro/oscuro, sin CDN ni dependencias de red. Se puede abrir desde el repo y exportar a PNG/SVG.

`README.md` incluye:
- fecha, commit y versión;
- índice de diagramas;
- qué decisiones representa cada uno;
- enlaces al plan y a los contratos;
- comando de regeneración.

---

## 2. Diagramas

### 2.1 `00-system-context.html`

**Pregunta:** ¿dónde vive cada módulo y qué habla con qué?

Elementos:
- Web Next.js: proceso, hechos, auditoría, chat.
- API FastAPI: auth, casos, query, chats, audits, facts, graph.
- Workers: legal_extraction, graph_build, indexing, second_opinion, facts_build.
- PostgreSQL + pgvector: tablas actuales y nuevas.
- Redis/Celery: colas.
- MCP: tools de lectura.
- Modelos: respondedor, verificador, segunda opinión.
- Object storage: originales, CKP.
- Límites: RLS, tenant, solo lectura de auditores.

Decisiones visuales:
- Un color por capa, no por marca.
- Flechas etiquetadas con el tipo de tráfico (`HTTP`, `job`, `SQL`, `tools`).
- Zona punteada para “solo lectura” de auditores.

### 2.2 `01-legal-guard-flow.html`

**Pregunta:** ¿cómo se decide publicar una respuesta?

Flujo:
```text
Pregunta
 -> política
 -> retrieval
 -> claims + citas
 -> citas válidas
 -> cifras/fechas
 -> partes/roles
 -> normas/jurisdicción
 -> decisión/contradicción
 -> juez semántico
 -> PASSED/WARNING/BLOCKED/NEEDS_REVIEW
 -> compositor
 -> answer_audit + UI
```
- Cada caja tiene el archivo/función que la implementa.
- Las cajas rojas son bloqueos; las ámbar, límites; las verdes, salida.
- Se muestra el punto exacto donde se elimina un claim.
- Se muestra que el juez no reemplaza al determinista.

### 2.3 `02-second-opinion-sequence.html`

**Pregunta:** ¿quién ve qué y en qué orden?

Secuencia:
```text
UI -> API: solicitar
API -> DB: run QUEUED
Worker -> DB: run RUNNING
Worker -> Modelo B: respuesta independiente (sin respuesta A)
Worker -> Modelo B: comparación (con A y B)
Worker -> DB: run COMPLETED + diferencias
UI -> API: consultar
UI -> Abogado: veredicto y diferencias
```
- Línea de tiempo con actor, mensaje y persistencia.
- Se marca explícitamente “fase 1 ciega”.
- Se muestra el caso de error y cancelación.
- Se muestra que no hay tools de escritura.

### 2.4 `03-facts-lifecycle.html`

**Pregunta:** ¿cómo pasa un hecho de alegación a probado?

Estado:
```text
ALLEGED
  -> EVIDENCE_SUPPORTED
  -> DISPUTED
  -> ADMITTED
  -> JUDICIALLY_PROVEN
  -> REJECTED
  -> UNRESOLVED
```
- Transiciones permitidas y condición de cada una.
- `JUDICIALLY_PROVEN` solo desde decisión con documento.
- `ADMITTED` solo desde posición `ADMITS`.
- `EVIDENCE_SUPPORTED` no es “probado”.
- Revisión humana y `fact_revisions` como caja lateral.
- Se muestra qué pasa si falta cita: no se crea el hecho.

### 2.5 `04-data-model.html`

**Pregunta:** ¿cómo se relacionan las tablas nuevas?

Entidades:
- `cases`, `case_folders`, `documents`, `document_pages`.
- `claims`, `facts`, `fact_positions`, `fact_revisions`.
- `evidence`, `evidence_links`, `decisions`, `legal_rules`, `citations`.
- `answer_audits`, `answer_audit_claims`, `answer_audit_citations`, `answer_audit_findings`.
- `second_opinion_runs`, `second_opinion_claims`, `second_opinion_findings`.
- `graph_nodes`, `graph_edges`, `chunks`.
- `reviews`, `audit_logs`.

Estilo:
- Cardinalidades explícitas.
- `organization_id` y `case_id` visibles en las tablas que aplican.
- RLS marcado con un candado.
- Append-only marcado con un reloj.
- No dibujar todas las columnas; las claves y los estados.

### 2.6 `05-ux-navigation.html`

**Pregunta:** ¿cómo se navega sin convertir el producto en tres silos?

Elementos:
- Sidebar con grupos Operación/Administración.
- Proceso con tabs Expediente/Hechos/Auditoría/Cronología.
- Chat global con badge de auditoría.
- Flujo de segunda opinión.
- Wireframes desktop/tablet/mobile.

Estilo:
- Reutiliza los wireframes de la guía de diseño.
- Marca qué es desktop, tablet y móvil.
- No es un diagrama de arquitectura; es un mapa de navegación.

---

## 3. Reglas de generación

1. Usar la skill **archify** como generador principal.
2. Cada diagrama parte de una fuente estructurada (Mermaid o JSON de nodos/aristas) guardada junto al HTML.
3. HTML autónomo, SVG inline, sin CDN.
4. Tema claro/oscuro con los tokens actuales (`oklch`), no una paleta nueva.
5. Etiquetas en español; términos técnicos en inglés solo si son nombres de tabla/tool.
6. No incluir datos reales de expedientes, nombres de personas ni radicados.
7. No incluir cifras de rendimiento no medidas.
8. Cada caja debe apuntar a un archivo/función real o a una tabla/migración del plan.
9. Validar que los HTML abren en navegador y que el SVG no tiene overflow.
10. Exportar PNG de cada diagrama para documentación y revisión.

---

## 4. Actualización de documentos

| Documento | Cambio |
|---|---|
| `docs/CHAT_PIPELINE_CONTRACT.md` | Añadir gate legal, `answer_audit`, `auditing` en SSE, segunda opinión. |
| `docs/MCP_PARITY.md` | Añadir tools nuevas y allowlists. |
| `docs/SSD_Judicial_AI_Platform.md` | Añadir un **addendum** al final, no reescribir 4.000 líneas. |
| `docs/PLAN_MODULOS_LEGALES_2026-10-22.md` | Enlazar los diagramas. |
| `docs/CONTRATOS_HARNESS_MODULOS_LEGALES.md` | Enlazar los diagramas de runtime. |
| `README.md` | Enlace a `docs/architecture/README.md`. |
| `docs/DEPLOY_GCP.md` | Añadir migraciones/backfill/flags y orden. |

### Addendum al SSD

Se crea `docs/ADDENDUM_MODULOS_LEGALES.md` con:
- propósito;
- cambios de esquema;
- nuevas reglas de publicación;
- segunda opinión;
- hechos y taxonomía;
- impacto en SSD §20 (answer contract), §33 (verificación), §34 (revisión), §39 (chunks), §108 (evidence matrix), §141 (CKP);
- enlaces a los diagramas.

No se edita el SSD completo para evitar deriva.

---

## 5. Criterios de aceptación

- [ ] `docs/architecture/README.md` existe y lista los 6 diagramas.
- [ ] Los 6 HTML abren sin red y sin errores de consola.
- [ ] Tema claro/oscuro funciona.
- [ ] SVG sin overflow en 1280 y 390 px.
- [ ] Cada caja/flujo apunta a código, tabla o decisión existente.
- [ ] No hay datos reales ni cifras inventadas.
- [ ] `CHAT_PIPELINE_CONTRACT`, `MCP_PARITY`, addendum y README enlazan los diagramas.
- [ ] El diagrama de datos coincide con las migraciones 0055-0058.
- [ ] El diagrama de hechos coincide con la taxonomía de la sección 6 del plan.
- [ ] El diagrama de segunda opinión muestra la fase 1 ciega.
- [ ] El diagrama de UX coincide con la guía de diseño.
- [ ] Se exportan PNG/SVG para revisión legal.
