# Auditoría de estado: módulos legales

**Fecha de cierre:** 2026-10-22  
**Alcance:** revisión de `PLAN_MODULOS_LEGALES_2026-10-22.md`, `CONTRATOS_HARNESS_MODULOS_LEGALES.md`, `GUIA_DISENO_MODULOS_LEGALES.md`, `ARCHIFY_MODULOS_LEGALES.md`, código, corpus y base local.  
**Base:** commit `0059960` más el cierre coordinado P0/P1/P2 de esta pasada.

---

## 1. Cerrado y verificado

### P0 — corpus, checksums, E2E/golden y despliegue

| Punto | Evidencia |
|---|---|
| Corpus | **143 normas por organización**, **286 filas** en Alfa y Beta, **29 fuentes**. Cero filas sin `checksum` o `official_url`. |
| Normas añadidas | Ley 1579/2012, Decreto 2723/2014, Estatuto Tributario, Código Civil adicional, 28 disposiciones del CGP, Ley 527/1999, Ley 2213/2022, CPACA, Ley 1394/2010, Ley 962/2005, Ley 590/2000, Ley 1581/2012, Ley 270/1996, Decretos 019/2012, 525/2009, 1069/2015, 650/1996, 2150/1995, 806/2020 y 960/1970. |
| Alias | `839-01` → `839-1` y `784-5` → artículo 784, numeral 5, con el mismo texto oficial y metadata `alias_of`. No se inventó una norma nueva. |
| `checksums.json` | `rules_count=143`, `sources_count=29`, `corpus_sha256=82e6c72d2548e694ae5f04ee881a4b5a07592b2fd0797778d8464d6543b91688`. `build_checksums.py --check` pasa e es idempotente. |
| Acta | `docs/ACTA_REVISION_CORPUS_LEGALES.md` separa trazabilidad técnica de firma jurídica humana. La firma sigue **pendiente**. |
| E2E | **24/24** Playwright en 360/768/1024/1440: pestañas, hechos, auditoría, segunda opinión interceptada, responsive y manifiesto. |
| Golden set | **7/7**: norma existente, inexistente, cifra, fecha, parte, cita válida y segunda opinión. |
| Migraciones | `0054 → 0064` en base desechable; downgrade `0064 → 0054` OK. Head local: `0064_legal_hold_release`. |
| Despliegue | `scripts/deploy_legal_modules.ps1` con dry-run por defecto, backup, migración, corpus, backfill, reindex, grafo, smoke y rollback. **Producción no fue desplegada**: falta target/credenciales/backup verificables; el script aborta sin `-AuthorizeProduction`. |

### P1 — gate, segunda opinión, hechos y UI

| Punto | Evidencia |
|---|---|
| Artículo exacto | El lookup no confunde el 422 con el 1422 y prioriza el cuerpo normativo citado. |
| Semántica | `check_article_supports_claim` detecta consecuencia no respaldada, solapamiento bajo, artículo de otro código y texto insuficiente. Juez LLM por claim que solo puede degradar. |
| Identificadores | `839-01`, `784-5`, rangos `10-16`, `10 a 16`, listas `10 y 16` y artículo punteado `2.2.6.1.2.4.1` resueltos. Rango con artículo faltante falla cerrado. |
| `quote_hash` | `QUOTE_MISMATCH` crítico; hash sin texto verificable queda en revisión, no se aprueba a ciegas. |
| Contradicciones | Una contradicción abierta omitida marca el claim `NEEDS_REVIEW`; no se elige la versión verdadera. La respuesta publica el claim con advertencia en vez de borrar toda la respuesta. |
| Juez persistido | `answer_audit_claims` guarda la fila `auditor='JUDGE'` por claim, con hallazgos asociados. |
| Segunda opinión | Comparador LLM con el prompt `second_opinion_compare.v1.md`, fallback determinista, modo LLM persistido, `auto_should_run`/`request_auto`, presupuesto `SECOND_OPINION_MAX_COST` y `require_second_opinion`. |
| Costo | Usa precios del modelo si existen; si no, queda explícitamente `cost_source='ESTIMATED'`. |
| Hechos | `extract_positions_from_claims` exige hablante atribuible y verbo explícito; no convierte `UNKNOWN` en `ADMITS`. En el caso local no cambió posiciones porque no hay claims con hablante resuelto; no se forzó atribución. |
| Evidencia | Trigger de `EVIDENCE_SUPPORTED`/`ADMITTED`/`JUDICIALLY_PROVEN` en migración `0062`; el flag `FACTS_PROOF_ENABLED=false` impide transiciones probatorias por API. |
| UI | `AuditBadge` en cada respuesta del chat y `SecondOpinionDiff` lado a lado por claim, con estados vacío/error/cancelado. |

### P2 — Archify y documentación

| Punto | Evidencia |
|---|---|
| Archify | **6/6**: contexto, gate, segunda opinión, hechos, modelo de datos y navegación UX. `validate`, `deliver`, `check --require-provenance` y `browser-check` pasan. |
| Prompts/schemas | Nombres del plan creados; `validate_schemas.py` PASS (20 metaschemas y 11 casos). Los prompts v4/v3 son contratos de versión, no prompts activos duplicados. |
| Flags | `LEGAL_GUARD_ENABLED`, `SECOND_OPINION_ENABLED` y `FACTS_PROOF_ENABLED` tienen default `false`; `.env`/`.env.test` locales los activan explícitamente. `.env.example` documenta las 15 variables que faltaban. |
| Documentación | Plan, implementación, guía de diseño, contratos, Archify e índice alineados con el estado real y el rango `0059–0064`. |

---

## 2. Validación ejecutada

| Comando | Resultado |
|---|---|
| `pytest tests/unit tests/integration tests/static tests/behavior tests/security -q` | **1308 passed**, 7 warnings |
| `pytest tests/static -q` | **174 passed** |
| `pytest tests/unit/test_legal_modules.py tests/integration/test_legal_modules_api.py -q` | **27 passed** |
| `evals/golden/legal_modules_runner.py` | **7/7 PASS** |
| `npm test` en `tests/e2e` | **24/24 passed** |
| `npm run build` en `apps/web` | Correcto |
| Docker local | API/web healthy; worker, worker-media y MCP healthy |
| Corpus SQL | 286 filas activas, 143 por organización, 29 fuentes, 0 sin checksum/URL |

La suite completa también verificó los contratos legacy: `/facts` sigue devolviendo lista por defecto (`envelope=true` es opt-in para la UI), el cache de test está desactivado y las auditorías de sistema llevan actor.

---

## 3. Bloqueos y límites reales

1. **Firma jurídica humana del corpus: pendiente.** No se puede sustituir con una firma automática. El acta contiene el formato y la evidencia técnica, pero la aceptación legal no está firmada.
2. **Producción no desplegada.** No hay target, credenciales ni backup de producción en esta sesión. El runbook y el script están probados en seco; no se declara un despliegue que no ocurrió.
3. **Cobertura residual.** El corpus cubre las normas con artículo concreto citadas por el expediente, pero no las que aparecen solo por nombre o en notas editoriales: Decreto 196/1971, Decreto 2364/2012, Decreto 1074/2015, Decreto 1469/2010, Ley 1285/2009, Ley 223/1995, Ley 2220/2022 y Ley 6/1992, además de concordancias sin número. El gate las bloquea; no las inventa.
4. **Precios de proveedor.** `ai_models` no tiene tarifas cargadas; el costo de segunda opinión es una estimación explícita, no un precio facturado.
5. **Posiciones de hechos.** Las 585 posiciones locales siguen `UNKNOWN` porque la extracción no encontró hablantes resueltos. Es el comportamiento seguro; requiere datos/revisión para pasar a `ALLEGES`/`DENIES`/`ADMITS`.
6. **Producción legal completa.** No se puede afirmar mientras sigan abiertos los puntos 1–3. El sistema **falla cerrado** y no publica una conclusión jurídica sin norma, cita, decisión o segunda opinión cuando la política lo exige.

---

## 4. Veredicto

Los tres módulos quedaron implementados, integrados y probados en local: antialucinación con gate determinista y juez, segunda opinión real con modelo distinto, hechos con invariantes y UI de auditoría/diff. El corpus pasó de 38 a **143 normas por organización** y hay E2E/golden set verificados.

La afirmación correcta es: **listo para revisión legal y despliegue controlado**, no “producción legal completa”. Los bloqueantes restantes son externos al código: firma jurídica, credenciales/backup de producción y ampliación de las normas que el expediente cita sin artículo verificable.
