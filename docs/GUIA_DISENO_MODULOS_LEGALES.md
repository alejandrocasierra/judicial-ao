# Guía de diseño: verificación, segunda opinión y hechos

**Versión:** 1.0  
**Fecha:** 2026-10-22  
**Base:** commit `963297c`  
**Plan maestro:** [PLAN_MODULOS_LEGALES_2026-10-22.md](PLAN_MODULOS_LEGALES_2026-10-22.md)

Esta guía no cambia la identidad del producto. Ordena las nuevas pantallas para que se sientan parte de Judicial AI y no un injerto de tres módulos. La dirección es **expediente jurídico sobrio, denso y verificable**, no un dashboard genérico de “IA”.

---

## 1. Lectura del producto

**Lectura:** plataforma jurídica de revisión documental para abogados y equipos de caso, con lenguaje visual editorial y funcional, dial **ENERGY 1 / RHYTHM 2 / MOTION 1**.

- **ENERGY 1:** la interfaz no grita. El peso está en el texto, la fuente y el estado.
- **RHYTHM 2:** el expediente mantiene una retícula estable; hechos y auditoría rompen con columnas y paneles, no con decoración.
- **MOTION 1:** transiciones cortas para cambios de estado; sin parallax, sin rebotes, sin animaciones de “IA”.

La dirección ya existe en el producto: tokens OKLCH monocromos, Geist, sidebar de 256 px, tarjetas con borde, modo claro/oscuro y estados ámbar/rosa. No se introduce una paleta nueva.

---

## 2. Principios

1. **La evidencia manda.** La fuente primaria se ve antes que la conclusión.
2. **Un estado se lee sin color.** Todo badge tiene texto e icono; el color es refuerzo.
3. **No hay porcentajes de verdad.** Se muestran conteos, hallazgos y citas.
4. **Densidad con aire.** El abogado necesita ver mucho sin perder jerarquía.
5. **La acción peligrosa se ve peligrosa.** Bloqueado, inadmisible, sin prueba y override no comparten el mismo tratamiento.
6. **Móvil no es una tabla encogida.** Hechos y auditoría se convierten en tarjetas y acordeones.
7. **Nada decorativo que no explique.** Sin gradientes, sin glass, sin orbes, sin iconos de “magia”.

---

## 3. Tokens y sistema visual

### 3.1 Colores

Se conservan los tokens de `apps/web/src/app/globals.css`.

| Rol | Token | Uso |
|---|---|---|
| Fondo | `background` | Lienzo |
| Superficie | `card` | Tarjetas, paneles |
| Texto | `foreground` | Proposición, títulos |
| Texto secundario | `muted-foreground` | Metadatos, fuentes |
| Borde | `border` | Separación |
| Acento | `primary` | Acción principal, foco |
| Verificado | `emerald-600/400` | `PASSED`, `JUDICIALLY_PROVEN`, `ADMITTED` |
| Advertencia | `amber-600/400` | `WARNING`, `EVIDENCE_SUPPORTED`, `NEEDS_REVIEW` |
| Bloqueo | `rose-600/400` | `BLOCKED`, `CRITICAL`, `REJECTED` |
| Neutro | `muted` | `SIN AUDITAR`, `ALLEGED`, `UNKNOWN` |

Reglas:
- El color de estado nunca es el único portador. Ejemplo: `Probado` + icono de sello; `Bloqueado` + icono de alerta.
- No se usa rojo para “eliminar” y para “bloqueado” a la vez sin texto.
- Contraste mínimo 4.5:1 para texto normal y 3:1 para texto grande.
- No se añaden gradientes ni sombras nuevas.

### 3.2 Tipografía

- `Geist` para UI; `Geist Mono` solo para radicados, códigos y hashes.
- Títulos de proceso: 24-28 px, peso 700.
- Títulos de panel: 16-18 px, peso 600.
- Proposición de hecho: 14-15 px, line-height 1.5.
- Metadatos y fuentes: 11-12 px, `muted-foreground`.
- No usar mayúsculas con tracking amplio para etiquetas; se usa sentence case.

### 3.3 Espaciado y radios

- Escala: 4, 8, 12, 16, 24, 32 px.
- Radios: 6 px para inputs/botones, 8 px para tarjetas/paneles.
- No todo es pill. Los badges son rectangulares con radio 4-6 px.
- Tablas: filas de 44-52 px; en móvil se convierten en tarjetas.

### 3.4 Iconografía

- Lucide existente, tamaño 14-16 px.
- Iconos semánticos: `ShieldCheck` verificado, `AlertTriangle` advertencia, `Ban` bloqueado, `Scale` hecho, `FileText` fuente, `Clock` tiempo.
- No usar sparkles, magia, robot ni diamantes para funciones legales.

---

## 4. Navegación y sidebar

### 4.1 Sidebar global

Mantener 256 px, drawer en móvil, pero agrupar:

```text
OPERACIÓN
  Dashboard
  Procesos
  Chats IA

ADMINISTRACIÓN
  Usuarios
  Roles y permisos
  Agentes
  Skills
  Modelos IA
  Verificación
  Backups
  Alertas
```

- `Verificación` es la política global y los modelos de segunda opinión.
- Los módulos por proceso no van en el sidebar global; viven en las pestañas del proceso.
- `aria-current="page"` en el ítem activo.
- Objetivo táctil mínimo 44 px.
- El drawer cierra al navegar; `Escape` cierra.
- En escritorio, la sidebar no se colapsa en esta fase; se puede añadir colapso después, no es requisito.

### 4.2 Pestañas del proceso

Debajo del header del proceso, sticky en desktop:

```text
[Expediente] [Hechos] [Auditoría] [Cronología]
```

- `Expediente`: carpetas, archivos, documentos, visor OCR/ASR.
- `Hechos`: ledger por parte.
- `Auditoría`: auditorías del proceso y segunda opinión.
- `Cronología`: abre el diálogo actual de timeline.
- En móvil, scroll horizontal con indicador de borde; no se apilan.
- La pestaña activa tiene borde inferior `primary` y texto `foreground`; las inactivas `muted-foreground`.

---

## 5. Layout por pantalla

### 5.1 Expediente (sin cambios estructurales)

- Header: volver, título, radicado, acciones, `Partes`, `Subir`.
- Breadcrumb de carpetas.
- Buscador y lista.
- El chat global sigue flotando; no se duplica.

### 5.2 Hechos

**Desktop (lg):**

```text
┌──────────────────────────────────────────────────────────────┐
│ Proceso · radicado · estado           [Partes] [Subir]       │
│ [Expediente] [Hechos] [Auditoría] [Cronología]               │
├──────────────────────────────────────────────────────────────┤
│ Hechos del proceso                       [Filtros] [Reconstruir]│
│ [Probados] [Con prueba] [Discutidos] [Todos]                  │
│ ┌───────────────────────────┬──────────────────────────────┐ │
│ │ Hecho                     │ Posiciones                   │ │
│ │ Proposición...            │ Demandante  Alega            │ │
│ │ [Probado] [Fuente p.2]    │ Demandado   Niega            │ │
│ │ [Ver evidencia] [Revisar] │ [Fuente]                     │ │
│ └───────────────────────────┴──────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

- Lista de tarjetas a ancho completo, con dos columnas internas: proposición/estado y posiciones/fuentes.
- Filtros en una barra: estado, parte, issue, búsqueda.
- Toggle de lado: `Demandante`, `Demandado`, `Ambos`; el default es `Ambos`.
- `Probados` es el default; muestra solo `ADMITTED` y `JUDICIALLY_PROVEN`.
- `Con prueba` muestra `EVIDENCE_SUPPORTED`.
- `Discutidos` muestra `DISPUTED`.
- `Todos` incluye `ALLEGED`, `UNRESOLVED`, `REJECTED`.
- El detalle se abre en un `Sheet` lateral en desktop, full-screen en móvil.

**Tablet (md):** una columna; las posiciones se muestran debajo de la proposición.

**Móvil:**

```text
[Hechos] [Filtros]
[Probados] [Con prueba] [Discutidos]
┌─────────────────────────────┐
│ Proposición...              │
│ [Probado] [Fuente p.2]      │
│ Demandante: Alega           │
│ Demandado: Niega            │
│ [Ver evidencia] [Revisar]   │
└─────────────────────────────┘
```

- Sin tabla horizontal.
- Acciones en una barra inferior sticky si hay selección.
- Botones de 44 px.

### 5.3 Auditoría del proceso

**Desktop (lg):**

```text
┌──────────────────────────────────────────────────────────────┐
│ Auditoría del proceso                    [Política] [Actualizar]│
│ Resumen: 12 auditadas · 2 advertencias · 0 bloqueadas          │
├───────────────────────────────┬──────────────────────────────┤
│ Lista de respuestas           │ Detalle de auditoría          │
│ - Pregunta...                 │ [Verificado] 2 claims         │
│   [Verificado] 2 fuentes      │ Claims: 2 soportados, 0 ...   │
│ - Pregunta...                 │ Findings: ...                 │
│   [Advertencia]               │ [Solicitar segunda opinión]  │
└───────────────────────────────┴──────────────────────────────┘
```

- Lista a la izquierda, detalle a la derecha.
- En tablet, la lista arriba y el detalle debajo.
- En móvil, acordeón por auditoría.
- El detalle muestra: estado, modo, política, claims, citas, findings, modelo, tiempo, botón de segunda opinión.
- No se muestra un score único; se muestran `claims soportados / total`, `findings críticos`, `citas válidas`.

### 5.4 Segunda opinión

**Desktop:** dos columnas 50/50, con cabecera de veredicto arriba.

```text
┌──────────────────────────────────────────────────────────────┐
│ Segunda opinión · [AGREE/PARTIAL/DISAGREE]                   │
│ Modelo A: ...        Modelo B: ...        [Cancelar]         │
├───────────────────────────┬──────────────────────────────────┤
│ Respuesta original        │ Respuesta independiente          │
│ ...                       │ ...                              │
├───────────────────────────┴──────────────────────────────────┤
│ Claims: Coincide | Falta | Sobra | Contradice | Incierto      │
│ [Revisar diferencias]                                         │
└──────────────────────────────────────────────────────────────┘
```

- En móvil: veredicto, hallazgos, luego acordeones “Original” e “Independiente”.
- El botón `Solicitar` se deshabilita si no hay modelo distinto configurado; muestra CTA a `Verificación`.
- El estado `Pendiente` no es un spinner infinito: muestra cola, tiempo y opción cancelar.

### 5.5 Chat

- Cada respuesta asistente lleva un badge pequeño debajo del texto:
  - `Verificado`, `Advertencia`, `Bloqueado`, `Sin auditar`.
- Click abre el panel lateral de auditoría.
- En el panel, `Solicitar segunda opinión`.
- No se llena el chat de botones; el badge y el panel son suficientes.
- El timeline de etapas existente se conserva; se añade `Auditando la respuesta...` cuando corresponda.

### 5.6 Administración de verificación

- Ruta `/dashboard/verificacion`.
- Secciones:
  - Política global: modo, bloqueos, umbrales, segunda opinión.
  - Modelos: respondedor vs verificador; validación de modelo distinto.
  - Rules: lista de códigos y severidades, solo lectura.
  - Estado: últimas auditorías, errores, costo.
- Formulario con `Guardar`, `Probar política` (dry-run con una respuesta de ejemplo) y `Restaurar`.
- No se permite guardar una política sin modelo distinto si `require_second_opinion=true`.

---

## 6. Componentes y estados

| Componente | Props mínimas | Estados |
|---|---|---|
| `ProcessTabs` | `caseId`, `active` | default, hover, focus, active |
| `FactsBoard` | `caseId`, `proof`, `party`, `query` | loading, empty, error, filtered |
| `FactCard` | `fact` | default, selected, blocked, review |
| `ProofBadge` | `status` | probado, admitido, con prueba, discutido, sin prueba, rechazado |
| `PartyPositionBadge` | `side`, `position` | alega, niega, admite, sin posición |
| `FactDetailSheet` | `factId` | loading, error, read-only, editing |
| `AuditBadge` | `status` | verificado, advertencia, bloqueado, sin auditar |
| `AuditPanel` | `auditId` | loading, error, passed, warning, blocked |
| `AuditFindingRow` | `finding` | crítica, alta, media, baja |
| `SecondOpinionDialog` | `messageId` | config missing, queued, running, done, failed |
| `SecondOpinionDiff` | `run` | agree, partial, disagree, inconclusive |
| `VerificationPolicyForm` | `policy` | loading, dirty, saving, error |

### 6.1 Empty states

- Sin hechos: “Aún no hay hechos extraídos para este proceso.” Botón `Reconstruir hechos` si hay documentos/claims; si no, CTA a subir documentos.
- Sin auditorías: “Aún no hay respuestas auditadas.” CTA a chat.
- Sin segunda opinión: “Configura un modelo distinto para auditar con una segunda opinión.” CTA a Verificación.
- Sin fuente: “Este hecho no tiene fuente primaria.” No se muestra como probado.

### 6.2 Loading

- Skeletons con la forma de la tarjeta/fila.
- Texto de estado real, no porcentaje inventado.
- En segunda opinión: cola, modelo, tiempo transcurrido.

### 6.3 Error

- Mensaje claro, `request_id` si existe, botón `Reintentar`.
- No se pierde el contexto ni el formulario.
- Error de permiso: explicar rol requerido, no “Error 403”.

### 6.4 Permisos

- Sin `audit.read`: no se muestra el panel; se muestra “No tienes permiso para ver auditorías”.
- Sin `facts.write`: botones de edición ocultos o deshabilitados con tooltip.
- Sin modelo: CTA de configuración, no error técnico.

---

## 7. Responsive

| Breakpoint | Sidebar | Proceso | Hechos | Auditoría | Segunda opinión |
|---|---|---|---|---|---|
| `<640` | drawer | header compacto, tabs scroll | tarjetas, 1 columna | acordeón | veredicto + acordeones |
| `640-767` | drawer | tabs scroll | tarjetas | lista + detalle apilado | columnas apiladas |
| `768-1023` | drawer | tabs | 1 columna | lista arriba, detalle abajo | 2 columnas estrechas |
| `>=1024` | fija 256 | tabs | 2 columnas internas | 8/4 | 6/6 |
| `>=1440` | fija 256 | max-width 1440 | 2 columnas | 8/4 | 6/6 |

Reglas:
- `min-w-0` en contenedores flex para evitar overflow.
- Tablas solo en desktop; en móvil, cards.
- `overflow-x-auto` solo en tabs y tablas de claims, nunca en el body.
- Botones de 44 px en móvil.
- No se usan hover como única affordance.

---

## 8. Accesibilidad

- WCAG 2.2 AA.
- Contraste 4.5:1; estados no dependen solo del color.
- Foco visible en todos los interactivos; no `outline: none` sin reemplazo.
- `Tab`/`Shift+Tab` lógico; `Enter`/`Space` activan.
- Diálogos con `Escape`, foco atrapado y retorno al disparador.
- `aria-live="polite"` para el estado de auditoría y segunda opinión.
- Tablas con `caption`, `th scope`, y texto alternativo en badges.
- `prefers-reduced-motion`: desactivar transiciones no esenciales.
- Zoom 200% sin pérdida de contenido.
- Pruebas con lector de pantalla en hechos y auditoría.

---

## 9. Copy y tono

- Preciso, sobrio, sin promesas.
- “Hecho probado”, no “verdad absoluta”.
- “Con prueba, no probado” para `EVIDENCE_SUPPORTED`.
- “Discutido” para `DISPUTED`.
- “Sin posición” para `UNKNOWN`.
- “No obra prueba” en lugar de “no existe”.
- “La respuesta se bloqueó porque una cifra no coincide con la fuente citada”, no “error de IA”.
- “Según la fuente”, “según la parte”, “según la decisión”.
- No usar “la IA determinó”, “100% confiable”, “verdad verificada” sin auditoría.
- No usar em dash en el texto nuevo.

---

## 10. Motion

- Transiciones de 150-200 ms para tabs, sheets y badges.
- Sin bounce, sin scale exagerado, sin animación de “pensando” infinita.
- La línea de etapas del chat existente se conserva; los nuevos estados usan el mismo patrón.
- En segunda opinión, un pulso discreto en el estado `running`, no un loader gigante.

---

## 11. Criterios de aceptación de diseño

- [ ] Desktop 1440: hechos y auditoría se leen sin scroll horizontal.
- [ ] Tablet 768: hechos en una columna; auditoría apilada.
- [ ] Móvil 360: tabs con scroll, cards, botones de 44 px, sin overflow.
- [ ] Teclado: se recorren tabs, filtros, cards, sheet y diálogo.
- [ ] Lector de pantalla: anuncia estado de auditoría y badges.
- [ ] Claro/oscuro: contraste y estados correctos.
- [ ] Empty, loading, error y permiso en cada pantalla.
- [ ] Sin controles muertos.
- [ ] Sin porcentajes de verdad ni métricas inventadas.
- [ ] Copy revisado por legal.
- [ ] El diseño se siente parte del producto actual, no un módulo externo.

---

## 12. Handoff a implementación

- `frontend-developer` implementa con `design-taste-frontend` y `frontend-ui-engineering`.
- `ui-designer` valida jerarquía, tokens y estados.
- `accessibility-auditor` valida teclado, contraste y lector.
- `e2e-runner` valida 360/768/1024/1440.
- `technical-writer` actualiza capturas y docs.
- Cualquier desviación de esta guía se registra como decisión de diseño y se aprueba antes de implementar.
