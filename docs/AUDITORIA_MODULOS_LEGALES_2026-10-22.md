# Auditoría de estado: módulos legales

**Fecha:** 2026-10-22  
**Alcance:** revisión de `PLAN_MODULOS_LEGALES_2026-10-22.md`, `CONTRATOS_HARNESS_MODULOS_LEGALES.md`, `GUIA_DISENO_MODULOS_LEGALES.md`, `ARCHIFY_MODULOS_LEGALES.md`, código y base local.  
**Commit revisado:** `9f1da7d` + cambios de esta pasada.

---

## 1. Cerrado en esta pasada

| Punto | Evidencia |
|---|---|
| **Corpus oficial piloto cargado** | `38` normas por organización (`76` filas en dos organizaciones), con `official_url`, `checksum`, `version_date` y texto. Fuentes: CGP/Ley 1564, Código Civil/Ley 57 y Código de Comercio/Decreto 410. |
| **Importación reproducible** | `scripts/import_legal_rules.py --dir data/legal_rules/co --all`; dry-run y checksums validados. |
| **Segunda opinión real** | Respondedor **Gemini 3.8 Flash**; verificador **DeepSeek Chat**; auditoría `PASSED`; run `COMPLETED`, veredicto `PARTIAL`, 2 claims originales frente a 5 del verificador, 4 s, 9.095 tokens de entrada, 444 de salida, costo registrado `0.005213`. |
| **Citas de normas persistidas** | `legal_guard.persist` ahora inserta `legal_rule_citations` para cada claim con `legal_rule_ids`. Verificado con el artículo 422 del CGP: `PASSED`, una cita de norma persistida y luego datos de prueba eliminados. |
| **Pruebas de los cambios** | `10 passed` en `tests/unit/test_legal_modules.py` y `tests/integration/test_legal_modules_api.py`. La suite completa sigue en `693` unitarias y `152` de integración. |

La sesión de segunda opinión quedó en la base local con el título `Segunda opinión real`, para que pueda revisarse en la UI. No se borró.

---

## 2. Gaps y bloqueantes

### P0: bloquean una afirmación de “producción legal completa”

| Gap | Estado | Impacto | Acción |
|---|---|---|---|
| **El corpus es piloto, no completo** | `38` normas por organización; cubre CGP, Código Civil y Código de Comercio, pero no todas las normas citadas por el expediente. Faltan, entre otras, Ley 1579 de 2012 (arts. 10-16 y 31), Decreto 2723 de 2014 (art. 22), Estatuto Tributario (arts. 630 y 839-01), Código Civil art. 769 y varias disposiciones del CGP. | El gate falla cerrado y bloqueará conclusiones que citen esas normas. No es una fuga de seguridad; es cobertura incompleta. | Completar `data/legal_rules/co/` con revisión legal y ejecutar el importador. No inventar texto ni aceptar artículos sin fuente. |
| **No hay firma/aceptación legal del corpus** | El texto se extrajo de páginas oficiales, pero no existe un acta de revisión jurídica por artículo ni un `checksums.json` agregado. | No se puede afirmar que el corpus sea el corpus oficial definitivo del piloto. | Revisión legal artículo por artículo; congelar checksums; registrar versión. |
| **Despliegue de producción no ejecutado** | Solo se migró y verificó local/test. No hay backup, migración, backfill ni rollback en producción. | El código está listo, pero producción no está actualizada. | Ventana con backup, `alembic upgrade`, backfill, reindex, rebuild de grafo y smoke test. |
| **E2E y golden set de los módulos legales ausentes** | Hay unitarias e integración; no hay Playwright 360/768/1024/1440 ni golden set específico de claims/normas/hechos. | No se puede declarar aceptación visual/legal completa; el plan exige G4/G5. | Añadir `tests/e2e` y un golden set con normas cargadas, casos de cifra/fecha/parte/norma y segunda opinión. |

### P1: gaps funcionales que afectan calidad, no la seguridad básica

| Gap | Evidencia / impacto | Acción |
|---|---|---|
| **El gate comprueba que la norma exista, pero no que el artículo diga lo que el claim afirma** | `_lookup_legal_rule` busca por identificador; no compara la proposición jurídica con el texto normativo. Un claim puede citar el artículo correcto y atribuirle una consecuencia falsa. | Añadir validación semántica de la norma contra el texto cargado y un juez específico; persistir veredicto `JUDGE`. |
| **No se valida `quote_hash` en la cita primaria** | `legal_guard` valida página/segmento y texto, pero no exige que el hash de la cita coincida con el texto fuente. | Exigir `quote_hash` cuando esté disponible y marcar `QUOTE_MISMATCH`. |
| **No se consultan `contradictions` en el gate** | El plan promete contradicciones; el código actual valida números/fechas/partes/normas/decisiones, no el catálogo de contradicciones. | Incorporar contradicciones abiertas al claim y exigir ambas versiones. |
| **El juez semántico no se persiste por claim con `auditor='JUDGE'`** | Se ejecuta la verificación existente, pero la auditoría persistida principal es determinista. | Persistir veredictos del juez y sus hallazgos, sin permitir que apruebe lo que el determinista rechazó. |
| **La fase 2 de segunda opinión es comparación determinista, no un comparador LLM** | `second_opinion.compare` usa solapamiento de términos y cifras. Es reproducible y barato, pero no interpreta diferencias semánticas. | Si se exige el comparador del plan, llamar al prompt `second_opinion_compare.v1.md` con modelo distinto y persistir su salida. |
| **`auto_second_opinion` no se ejecuta** | La columna existe, pero el pipeline no dispara automáticamente `ANALYSIS`/`ALL`/`STRICT_ONLY`. | Implementar el hook por intención y política, con idempotencia y límite de costo. |
| **`SECOND_OPINION_MAX_COST` y `require_second_opinion` no se aplican en el flujo** | Son configuración declarada, pero no hay guard de costo ni bloqueo de publicación. | Aplicar presupuesto antes de encolar y bloquear/avisar según política. |
| **Costo de segunda opinión es una fórmula fija** | `_cost()` usa una tarifa conservadora fija; no lee precio del modelo. | Añadir precios por modelo o marcar el costo como estimado. |
| **Posiciones de hechos inferidas quedan `UNKNOWN`** | Las `585` posiciones del proceso local se infirieron por mención de parte; no hay extracción de “alega/niega/admite” desde claims. | Ejecutar el Analista de hechos con citas y revisión humana; no convertir `UNKNOWN` en `ADMITS`. |
| **`EVIDENCE_SUPPORTED` no tiene invariante en BD** | El trigger solo exige decisión para `JUDICIALLY_PROVEN` y admisión para `ADMITTED`. | Añadir validación de evidencia admisible para `EVIDENCE_SUPPORTED` o dejarlo explícitamente como estado de revisión. |
| **UI de segunda opinión incompleta** | Hay panel de auditoría y botón, pero no `SecondOpinionDialog`/diff lado a lado ni comparación visual por claim. | Implementar el diff y estados vacío/error/cancelado. |
| **`AuditBadge` no está en el chat** | La auditoría se ve en la pestaña Auditoría, no en cada respuesta del chat. | Añadir badge con `audit_status` y enlace al detalle. |

### P2: documentación, Archify y deuda de plan

| Gap | Evidencia | Acción |
|---|---|---|
| **Archify: 4 de 6 diagramas** | Existen contexto, gate, segunda opinión y hechos. Faltan `04-data-model.html` y `05-ux-navigation.html`. | Generar y validar los dos restantes. |
| **El documento Archify conserva una frase obsoleta** | Dice “No existe hoy una carpeta docs/architecture” cuando ya existe. | Corregir el estado inicial y listar los 4/6 artefactos. |
| **El plan habla de migraciones 0055-0058** | La implementación real llegó a `0061` por permisos de worker y aristas de grafo. | Actualizar el rango y el inventario. |
| **Prompts/schemas del plan no existen con esos nombres** | Faltan `answer_question.v4.md`, `verify_answer.v3.md`, `fact_proof_review.v1.md`, `legal_audit.schema.json`, `second_opinion.schema.json` y `party_fact.schema.json`; existen prompts funcionales equivalentes. | Alinear nombres o corregir el inventario del plan. |
| **Flags por defecto** | El plan P0-03 pide flags off; `config.py` tiene `LEGAL_GUARD_ENABLED=true`, `SECOND_OPINION_ENABLED=true` y `FACTS_PROOF_ENABLED=true`. | Decidir política de despliegue; para producción, activar después de migrar. |
| **`data/legal_rules/co/checksums.json` no existe** | El plan lo pide; los checksums están por fila en JSON. | Generar el agregado o actualizar el plan. |
| **No hay `layout.tsx` de proceso** | Las pestañas se insertan en la página de expediente y en páginas hijas; no hay layout compartido. | Extraer layout si se busca consistencia total. |
| **`docs/PLAN` sigue diciendo “la implementación puede comenzar”** | El código ya está implementado en local. | Actualizar el estado de cierre y enlazar esta auditoría. |

---

## 3. Lo que no es un gap

- `cases` sigue siendo la unidad proceso/demanda.
- Las tablas nuevas tienen RLS y triggers de misma organización.
- `JUDICIALLY_PROVEN` y `ADMITTED` están protegidos por trigger; `EVIDENCE_SUPPORTED` no se publica como probado.
- Los `826` hechos locales tienen estado de prueba; `426` son judicialmente probados.
- Los `826` chunks `fact` tienen embeddings y el grafo tiene `19.641` aristas.
- La segunda opinión no cae al mismo modelo de forma silenciosa; exige configuración distinta.
- La exportación CKP 1.1 y la purga incluyen las tablas nuevas.

---

## 4. Orden recomendado para cerrar

1. **P0:** ampliar el corpus con las normas del expediente y firma legal; ejecutar importación en todas las organizaciones.
2. **P0:** golden set + E2E de hechos/auditoría/segunda opinión.
3. **P1:** validación semántica de normas, contradicciones y `quote_hash`; persistir juez.
4. **P1:** activar `auto_second_opinion` y presupuesto; completar UI de diff.
5. **P2:** generar los dos diagramas Archify restantes y corregir el estado documental.
6. **P0 producción:** backup, migración `0054 -> 0061`, backfill, reindex, grafo y smoke test.

---

## 5. Veredicto

El núcleo de los tres módulos funciona en local y la segunda opinión real está ejecutada. El sistema **falla cerrado** cuando falta una norma, un modelo o una decisión. No se puede declarar “producción legal completa” todavía: el corpus es un piloto de 38 normas por organización y faltan E2E/golden set y despliegue. Esos son los bloqueantes reales, no un fallo silencioso del gate.
