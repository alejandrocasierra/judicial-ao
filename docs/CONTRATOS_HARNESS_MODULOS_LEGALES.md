# Contratos de harness: módulos legales

**Versión:** 1.0  
**Fecha:** 2026-10-22  
**Base:** commit `963297c`  
**Plan maestro:** [PLAN_MODULOS_LEGALES_2026-10-22.md](PLAN_MODULOS_LEGALES_2026-10-22.md)

Este documento define **quién construye**, **quién ejecuta en runtime**, **qué recibe**, **qué entrega**, **qué tools puede usar**, **qué no puede hacer** y **qué gate debe pasar** cada pieza. No es una lista de deseos: cada contrato tiene un dueño, un artefacto y una condición de aceptación.

---

## 0. Cómo funciona el harness

### 0.1 Dos capas

| Capa | Qué es | Dónde vive |
|---|---|---|
| **Build harness** | Agentes de ingeniería que implementan, revisan y prueban el cambio. | Sesiones de desarrollo, subagentes, CI. |
| **Runtime harness** | Agentes y skills del producto que se ejecutan durante una consulta o auditoría. | `agents`, `skills`, `case_tools`, workers. |

No se mezclan. Un agente de construcción no se invoca en producción; un agente de runtime no decide la arquitectura ni escribe migraciones.

### 0.2 Secuencia de gates

```text
G0 Contrato y datos aprobados
 -> G1 Migración + RLS + backfill en seco
 -> G2 Núcleo determinista + unitarias
 -> G3 Integración API/DB + RLS + dos procesos
 -> G4 UI/E2E + responsive + accesibilidad
 -> G5 Golden set + segunda opinión + hechos
 -> G6 Seguridad + performance + migración/rollback
 -> G7 Aceptación legal y despliegue
```

Ningún gate se salta. Un FAIL devuelve la tarea al dueño anterior; no se “compensa” con otro módulo.

### 0.3 Regla de oro

> Un agente de runtime **propone**; el código determinista **decide**; el humano **responde** por el override.

Ningún agente puede:

- inventar una cita, una norma, una parte, una cifra o una decisión;
- marcar un hecho como probado sin admisión o decisión;
- escribir en `facts`, `claims`, `evidence`, `documents`, `decisions` o `legal_rules`;
- aprobar su propia salida;
- usar una tool fuera de su allowlist;
- ver datos de otro `case_id`.

---

## 1. Contratos de construcción

### HC-00: arquitectura, datos y ADR

| Campo | Contenido |
|---|---|
| **Dueño** | `architect` |
| **Apoyo** | `database-optimizer`, `backend-architect`, `security-architect` |
| **Skills** | `architecture-decision-records`, `api-and-interface-design`, `source-driven-development` |
| **Entradas** | Plan maestro, esquema actual, migraciones 0001-0054, consultas de `cases`, `facts`, `graph`, `chunks`. |
| **Salidas** | ADR de las decisiones D1-D30, ER de las tablas nuevas, contratos de API, matriz de migraciones. |
| **Tools** | Lectura de repo, `rg`, `psql` de solo lectura, generador de diagramas. |
| **Prohibido** | Cambiar el significado de `cases`; borrar tablas; introducir un singleton global; aprobar una migración sin downgrade. |
| **Gate** | G0: plan y ER revisados; toda tabla tiene `organization_id`, `case_id` cuando aplica, RLS y trigger same-org. |
| **Handoff** | A HC-01/03/04 con migraciones y DDL congelados. |

### HC-01: backend antialucinación

| Campo | Contenido |
|---|---|
| **Dueño** | `backend-architect` |
| **Apoyo** | `rag-pipeline-engineer`, `ai-engineer`, `prompt-engineer` |
| **Skills** | `test-driven-development`, `security-and-hardening`, `verification-loop`, `source-driven-development` |
| **Entradas** | DDL 0055, prompts v4/v3, `answering.py`, `verification.py`, `compose.py`, `query.py`, `chats.py`. |
| **Salidas** | `legal_guard.py`, `legal_rules.py`, `claim_grounding.py`, `audits.py`, integración SSE, tests. |
| **Tools runtime** | Sin tools de escritura. El servicio usa SQL de lectura/inserción de auditoría y las tools read-only del agente verificador. |
| **Prohibido** | Publicar un claim sin cita; aceptar una norma que no esté en `legal_rules`; dejar una respuesta sin `answer_audit`; usar el juez como única defensa. |
| **Gate** | G2 + G3: 100% de claims publicados con cita, 0 cifras/fechas/partes/normas inventadas, RLS, dos procesos. |
| **Handoff** | A HC-02 (UI) y HC-03 (segunda opinión) con `AuditOut` estable. |

### HC-02: UI de verificación y hechos

| Campo | Contenido |
|---|---|
| **Dueño** | `frontend-developer` |
| **Apoyo** | `ui-designer`, `ux-architect`, `accessibility-auditor` |
| **Skills** | `design-taste-frontend`, `frontend-ui-engineering`, `ui-ux-pro-max`, `impeccable` |
| **Entradas** | Contratos `AuditOut`, `SecondOpinionOut`, `FactOut`; guía de diseño; tokens actuales. |
| **Salidas** | `ProcessTabs`, `FactsBoard`, `FactDetailSheet`, `AuditPanel`, `SecondOpinionDialog`, `VerificationPolicyForm`; rutas y estados. |
| **Prohibido** | Inventar porcentajes; mostrar color sin texto; romper teclado; tablas con overflow en móvil; un módulo global por cada punto. |
| **Gate** | G4: E2E en 360/768/1024/1440, teclado, claro/oscuro, sin overflow, sin controles muertos. |
| **Handoff** | A HC-06 para e2e y a HC-07 para docs. |

### HC-03: segunda opinión

| Campo | Contenido |
|---|---|
| **Dueño** | `ai-engineer` |
| **Apoyo** | `prompt-engineer`, `backend-architect`, `database-optimizer` |
| **Skills** | `source-driven-development`, `verification-loop`, `test-driven-development` |
| **Entradas** | `answer_audits`, `second_opinion_runs`, `model_runs`, `ai_models`, evidencia primaria. |
| **Salidas** | `second_opinion.py`, worker, prompts independiente/comparador, API, tests de independencia. |
| **Prohibido** | Usar el mismo modelo; mostrar la respuesta original en fase 1; dar tools de escritura; fallback silencioso; bloquear el request HTTP. |
| **Gate** | G3 + G5: modelo distinto, fase 1 ciega, comparación persistida, p95 < 180 s, costo registrado. |
| **Handoff** | A HC-02 para comparación y a HC-06 para worker/e2e. |

### HC-04: hechos y posiciones de parte

| Campo | Contenido |
|---|---|
| **Dueño** | `data-engineer` |
| **Apoyo** | `database-optimizer`, `backend-architect`, `rag-pipeline-engineer` |
| **Skills** | `graphify`, `test-driven-development`, `source-driven-development` |
| **Entradas** | `claims`, `facts`, `fact_claims`, `evidence`, `decisions`, `parties`, OCR/ASR. |
| **Salidas** | `party_facts.py`, extracción, `fact_positions`, `fact_revisions`, proof, API, grafo, chunks, tests. |
| **Prohibido** | Marcar probado sin decisión/admisión; usar OCR/ASR bajo umbral como prueba; borrar historia; mezclar hechos de dos casos. |
| **Gate** | G3 + G5: 100% de probados con fuente, dos procesos aislados, grafo/chunks actualizados. |
| **Handoff** | A HC-02 (tablero) y HC-07 (CKP/Archify). |

### HC-05: seguridad y aislamiento

| Campo | Contenido |
|---|---|
| **Dueño** | `security-architect` |
| **Apoyo** | `security-reviewer`, `application-security-engineer` |
| **Skills** | `security-and-hardening`, `code-review-and-quality` |
| **Entradas** | DDL, endpoints, prompts, tools, workers. |
| **Salidas** | Amenazas, pruebas de RLS, prompt injection, tools, secretos, rate limit, costo, logs. |
| **Prohibido** | Aprobar tablas sin RLS; aceptar tools de escritura en auditores; guardar claves o prompts sensibles en logs. |
| **Gate** | G6: 0 hallazgos altos/críticos; pruebas de acceso cruzado; auditoría de dependencias. |
| **Handoff** | Firma para G7. |

### HC-06: QA, migración y rendimiento

| Campo | Contenido |
|---|---|
| **Dueño** | `test-automation-engineer` |
| **Apoyo** | `e2e-runner`, `database-reliability-engineer`, `performance-benchmarker` |
| **Skills** | `e2e-testing`, `browser-qa`, `test-driven-development`, `production-audit` |
| **Entradas** | Código, migraciones, fixtures, golden set, flags. |
| **Salidas** | Unitarias, integración, E2E, migración/rollback, performance, reporte. |
| **Prohibido** | Declarar PASS sin evidencia; probar solo con un proceso; usar datos reales no autorizados. |
| **Gate** | G6: suite completa, dos procesos, up/down, p95, 0 fallos. |
| **Handoff** | A G7. |

### HC-07: documentación, CKP y Archify

| Campo | Contenido |
|---|---|
| **Dueño** | `technical-writer` |
| **Apoyo** | `architect`, `archify` |
| **Skills** | `living-docs-governance`, `archify`, `documentation-and-adrs` |
| **Entradas** | Plan, contratos, esquema final, endpoints, UI. |
| **Salidas** | `CHAT_PIPELINE_CONTRACT.md`, `MCP_PARITY.md`, addendum al SSD, 6 diagramas Archify, CKP 1.1, README de operación. |
| **Prohibido** | Reescribir el SSD completo; documentar una tabla que no existe; diagramas sin fuente. |
| **Gate** | G7: docs enlazadas, diagramas abren, CKP exporta las nuevas tablas. |

---

## 2. Contratos de runtime

### RT-01: `Verificador legal`

| Campo | Contenido |
|---|---|
| **Nombre** | `Verificador legal` |
| **Tipo** | Agente de sistema, solo lectura, tarea `answer_audit`. |
| **Misión** | Auditar cada claim de una respuesta contra la evidencia primaria, las partes, las normas y las decisiones, y devolver un veredicto publicable. |
| **Entrada** | `question`, `claims[]`, `evidence[]` (handles, texto, fuente), `policy`, `parties`, `legal_rules`, `decisions`, `contradictions`. |
| **Salida** | JSON `legal_audit` (abajo). |
| **Tools** | `search_case`, `read_document`, `get_document_page`, `find_person`, `graph_query`, `get_claims`, `verify_citation`, `lookup_legal_rule`. |
| **Skills** | `Cita primaria`, `Cifras, fechas y montos`, `Partes y roles`, `Norma y jurisdicción`, `Contradicción en dos versiones`, `Decisión judicial`, `Abstención y límites`. |
| **Prohibido** | Escribir en cualquier tabla; aprobar sin cita; citar una norma no registrada; decidir cuál versión de una contradicción es verdadera; usar texto de otro caso. |
| **Gate** | Todo claim con `SUPPORTED` debe tener cita válida; `CRITICAL` bloquea; el veredicto determinista manda sobre el juez. |
| **Fallo** | Si el modelo o la tool falla, el determinista decide; se registra `AUDIT_MODEL_ERROR`; no se inventa un veredicto. |

**Salida `legal_audit`:**
```json
{
  "verdict": "PASSED | WARNING | BLOCKED | NEEDS_REVIEW",
  "claims": [
    {
      "index": 0,
      "status": "SUPPORTED | UNSUPPORTED | CONTRADICTED | NEEDS_REVIEW",
      "claim_type": "FACT | LEGAL_CONCLUSION | PROCEDURAL | PARTY_ALLEGATION | INFERENCE",
      "grounding": 0.94,
      "missing_numbers": [],
      "missing_dates": [],
      "missing_parties": [],
      "legal_rule_ids": [],
      "citation_handles": ["E1"],
      "reasons": []
    }
  ],
  "findings": [
    {
      "code": "NUMBER_MISMATCH",
      "severity": "CRITICAL",
      "claim_index": 1,
      "message": "El monto 8.400.000 no aparece en la página citada.",
      "blocking": true
    }
  ],
  "limits": [],
  "policy_version": 3
}
```

### RT-02: `Auditor de segunda opinión`

| Campo | Contenido |
|---|---|
| **Nombre** | `Auditor de segunda opinión` |
| **Tipo** | Agente de sistema, solo lectura, tarea `second_opinion`. |
| **Misión** | Responder de forma independiente y comparar con la respuesta original. |
| **Entrada fase 1** | `question`, `evidence[]`, `policy`, `case_id`; **no** la respuesta original. |
| **Entrada fase 2** | Respuesta original, claims independientes, evidencia, hallazgos. |
| **Salida** | JSON `second_opinion` (abajo). |
| **Tools** | `search_case`, `read_document`, `get_document_page`, `find_person`, `graph_query`, `get_claims`, `get_fact`, `verify_citation`, `lookup_legal_rule`. |
| **Skills** | `Respuesta independiente`, `Comparación probatoria`, `Disenso y revisión`. |
| **Prohibido** | Ver la respuesta original en fase 1; usar tools de escritura; cambiar hechos; repetir el mismo modelo; ocultar un desacuerdo. |
| **Gate** | Modelo distinto; fase 1 ciega; diferencias por claim; `DISAGREE` crítico -> `NEEDS_REVIEW`; la respuesta debe tener `answer_audit` previa. |
| **Fallo** | `FAILED` con `error_code`; no se inventa un veredicto; se puede reintentar. |

**Salida `second_opinion`:**
```json
{
  "independent": {
    "claims": [
      {"text": "...", "citations": ["E1"], "confidence": 0.91}
    ],
    "uncertainties": []
  },
  "comparison": {
    "verdict": "AGREE | PARTIAL | DISAGREE | INCONCLUSIVE",
    "summary": "Coinciden en el hecho principal; discrepan en el monto.",
    "differences": [
      {
        "claim_index": 2,
        "type": "DIFFERENT_NUMBER",
        "severity": "HIGH",
        "original": "$7.400.000.000",
        "second": "$7.400.000",
        "reason": "La fuente citada contiene la primera cifra."
      }
    ]
  }
}
```

### RT-03: `Analista de hechos`

| Campo | Contenido |
|---|---|
| **Nombre** | `Analista de hechos` |
| **Tipo** | Agente de sistema, solo lectura, tarea `party_facts_extract`. |
| **Misión** | Extraer hechos candidatos, posiciones de parte y vínculos probatorios, sin declarar probado nada. |
| **Entrada** | Páginas/segmentos, claims, parties, evidence, decisions, contradicciones. |
| **Salida** | JSON `party_facts` (abajo). |
| **Tools** | `search_case`, `read_document`, `get_document_page`, `find_person`, `graph_query`, `get_claims`, `get_fact`, `verify_citation`. |
| **Skills** | `Posición de parte`, `Prueba admitida`, `Hecho probado`, `Revisión humana`. |
| **Prohibido** | Escribir `JUDICIALLY_PROVEN`; declarar admisión sin texto; usar evidencia inadmisible; inventar una parte; borrar una revisión. |
| **Gate** | Cada hecho con cita o claim; `JUDICIALLY_PROVEN` solo con decisión; `ADMITTED` solo con posición `ADMITS`; dos casos aislados. |
| **Fallo** | Se descarta el candidato; se registra el motivo; no se persiste un hecho sin fuente. |

**Salida `party_facts`:**
```json
{
  "facts": [
    {
      "proposition": "El pagaré 77391469 se entregó para capitalizar intereses de plazo de 2009.",
      "fact_type": "PAYMENT | CONTRACT | DATE | AMOUNT | CONDUCT | STATUS",
      "party_side": "DEMANDANTE | DEMANDADO | TERCERO | NO_ATRIBUIDO",
      "party_name": "Jorge Humberto Rojas Melo",
      "position": "ALLEGES | DENIES | ADMITS | UNKNOWN",
      "claim_ids": ["..."],
      "citation_handles": ["E1"],
      "confidence": 0.88,
      "legal_issue": "capitalización de intereses"
    }
  ],
  "warnings": []
}
```

### RT-04: integración con el respondedor

El `Agente jurídico (anti-alucinación)` existente sigue respondiendo. El nuevo `Verificador legal` no lo reemplaza.

| Campo | Contenido |
|---|---|
| **Entrada** | Respuesta del respondedor, claims, evidencia, política. |
| **Salida** | `answer_audit` y claims aprobados. |
| **Regla** | El respondedor no puede publicar por fuera del gate. |
| **Handoff** | `query.py` -> `legal_guard` -> `compose` -> `chat_messages` -> UI. |

---

## 3. Skills de runtime

Las skills se siembran en `skills` y se enlazan a los agentes. Cada una tiene prompt, allowlist y parámetros.

| Skill | Agente | Prompt que aporta | Tools que habilita | Prohibición |
|---|---|---|---|---|
| `Cita primaria` | Verificador | Exige página/segmento y quote exacto | `verify_citation`, `read_document` | No aceptar “según el expediente” sin fuente |
| `Cifras, fechas y montos` | Verificador | Compara cada número con la cita | `read_document`, `get_document_page` | No redondear ni convertir montos |
| `Partes y roles` | Verificador | Valida nombres, alias y rol | `find_person`, `graph_query` | No crear partes |
| `Norma y jurisdicción` | Verificador | Busca en `legal_rules` | `lookup_legal_rule` | No citar de memoria |
| `Contradicción en dos versiones` | Verificador | Exige ambas versiones | `search_case`, `get_claims` | No elegir una verdad |
| `Decisión judicial` | Verificador | Exige decisión y documento | `read_document`, `get_claims` | No declarar probado sin decisión |
| `Abstención y límites` | Verificador | Devuelve límites claros | ninguna | No rellenar con opinión |
| `Respuesta independiente` | Auditor | Responde sin ver la original | tools de lectura | No comparar antes de responder |
| `Comparación probatoria` | Auditor | Compara claims/citas | tools de lectura | No ocultar diferencias |
| `Disenso y revisión` | Auditor | Genera findings | ninguna | No aprobar su propio disenso |
| `Posición de parte` | Analista de hechos | Extrae alega/niega/admite | tools de lectura | No atribuir sin cita |
| `Prueba admitida` | Analista de hechos | Distingue admisible de alegada | tools de lectura | No usar inadmisible |
| `Hecho probado` | Analista de hechos | Aplica taxonomía | tools de lectura | No probar sin decisión/admisión |
| `Revisión humana` | Analista de hechos | Prepara el checklist | ninguna | No aprobar por el humano |

---

## 4. Herramientas nuevas y paridad MCP

Añadir a `case_tools` y al servidor MCP:

| Tool | Entrada | Salida | Quién |
|---|---|---|---|
| `verify_citation` | `citation_id` o `document_id/page` o `segment_id` | `valid`, `reasons`, `source` | Verificador, Auditor, Analista |
| `lookup_legal_rule` | `jurisdiction`, `source`, `identifier`, `date` | regla activa o `not_found` | Verificador, Auditor |
| `get_fact` | `fact_id` | hecho, posiciones, evidencia, decisión | Auditor, Analista |
| `list_facts` | `case_id`, filtros | lista | Auditor, Analista |
| `get_answer_audit` | `audit_id` | auditoría + findings | UI/API, no agente |
| `get_second_opinion` | `run_id` | run + diferencias | UI/API |

Actualizar `docs/MCP_PARITY.md` y `TOOL_DESCRIPTIONS`. Ninguna tool nueva es de escritura.

---

## 5. Matriz RACI

| Actividad | Architect | Backend | DB | AI | Frontend | Security | QA | Docs |
|---|---|---|---|---|---|---|---|---|
| Decisiones D1-D30 | A/R | C | C | C | C | C | I | I |
| Migraciones/RLS | A | C | R | I | I | C | C | I |
| `legal_guard` | A | R | C | C | I | C | C | I |
| Segunda opinión | A | C | C | R | C | C | C | I |
| Hechos/proof | A | C | R | C | C | C | C | I |
| UI | A | C | I | I | R | C | C | I |
| Seguridad | A | C | C | C | C | R | C | I |
| Pruebas | A | C | C | C | C | C | R | I |
| Docs/Archify | A | C | C | C | C | C | C | R |

`R` = responsable, `A` = aprueba, `C` = consultado, `I` = informado.

---

## 6. Contratos de handoff

| Desde | Hacia | Entregable | Condición |
|---|---|---|---|
| Architect | DB | ER + DDL + decisiones | G0 |
| DB | Backend | Migraciones + RLS + tipos | G1 |
| Backend | AI | `AuditOut`, tools, prompts | G2 |
| AI | Frontend | `AuditOut`, `SecondOpinionOut`, `FactOut` | G3 |
| Frontend | QA | Rutas, componentes, estados | G4 |
| QA | Security | Suite + e2e + migración | G5 |
| Security | Docs | Reporte | G6 |
| Docs | Release | Docs + Archify + CKP | G7 |

---

## 7. Definición de terminado por contrato

### RT-01
- [ ] 100% de claims publicados con cita válida en golden set.
- [ ] 0 cifras/fechas/partes/normas inventadas.
- [ ] 0 `JUDICIALLY_PROVEN` sin decisión.
- [ ] 100% de respuestas con `answer_audit`.
- [ ] Override humano auditado.
- [ ] p95 determinista < 300 ms.

### RT-02
- [ ] Modelo distinto obligatorio.
- [ ] Fase 1 ciega.
- [ ] Comparación con diferencias por claim.
- [ ] Async, idempotente, cancelable.
- [ ] Costo y latencia registrados.
- [ ] Sin tools de escritura.
- [ ] p95 < 180 s.

### RT-03
- [ ] Hechos con fuente o claim.
- [ ] Posiciones de demandante y demandado.
- [ ] `ADMITTED`/`JUDICIALLY_PROVEN` con evidencia/decisión.
- [ ] `EVIDENCE_SUPPORTED` separado de probado.
- [ ] Revisiones append-only.
- [ ] Grafo/chunks actualizados.
- [ ] Dos procesos aislados.

### HC-05
- [ ] RLS y same-org en todas las tablas.
- [ ] Prompt injection mitigado.
- [ ] Tools read-only.
- [ ] Sin secretos en logs.
- [ ] 0 hallazgos altos/críticos.

### HC-06
- [ ] Unitarias, integración, e2e.
- [ ] Migración up/down.
- [ ] Backfill dry-run + real.
- [ ] Performance.
- [ ] Reporte de aceptación.

### HC-07
- [ ] Docs enlazadas.
- [ ] 6 diagramas Archify.
- [ ] CKP 1.1.
- [ ] `MCP_PARITY` actualizado.

---

## 8. Control de cambios

Cualquier cambio en estos contratos requiere:

1. Actualizar este documento.
2. Registrar la decisión en el plan maestro (D nuevos).
3. Avisar a los dueños de HC-01 a HC-07.
4. Re-ejecutar el gate afectado.
5. No cambiar el contrato de datos sin migración y prueba de rollback.

**Estado:** aprobado para implementación. No hay contratos pendientes.
