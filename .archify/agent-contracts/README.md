# Contratos de ejecución — Chat IA como investigador jurídico nativo

Este directorio es el contrato vinculante entre el responsable del proyecto y los
agentes que implementan el plan `.archify/implementation_plan.md`.

Regla de oro: **ninguna fase se declara terminada por “código escrito”**. Se
declara terminada cuando sus criterios de aceptación se cumplen con evidencia
reproducible (tests, salida de base de datos o artefacto en `var/eval/`).

## 1. Objetivo

Convertir el chat del expediente `11001310302120180036100`
(`case_id = 38865959-8d5f-44e4-bbc8-1226bd345b00`) en un investigador que:

1. enruta las preguntas jurídicas sustantivas al camino profundo (agente + tools);
2. investiga por facetas sobre OCR, ASR, `facts`, `claims`, contradicciones y grafo;
3. redacta un informe estructurado y conversacional, no una nota de una cita;
4. conserva toda afirmación con cita primaria válida y marca para revisión lo dudoso;
5. no presenta como respuesta el cartel “Encontré material relacionado…”.

## 2. Orden de ejecución y compuertas

| Orden | Fase | Contrato | Agente responsable | Skills que debe cargar | Compuerta de salida |
|---|---|---|---|---|---|
| 0 | Línea base y set dorado | `F0.md` | `test-automation-engineer` | `test-driven-development`, `verification-loop` | `var/eval/golden_before.json` + `.md` |
| 1 | Routing y presupuestos | `F1.md` | `senior-developer` | `incremental-implementation`, `test-driven-development` | tests de intención/config en verde |
| 2 | Agente investigador, `facts`, ASR y SSE | `F2.md` | `senior-developer` | `source-driven-development`, `incremental-implementation` | tests + smoke de `fact_seed` contra la BD |
| 3 | Síntesis estructurada y compositor narrativo | `F3.md` | `prompt-engineer` (prompts) → `senior-developer` (código) | `test-driven-development`, `incremental-implementation` | tests de prompt/parse/compose en verde |
| 4 | Verificador contextual sin amputación | `F4.md` | `senior-developer` | `test-driven-development`, `verification-loop` | tests de fechas/partes/publicación en verde |
| V | Verificación end-to-end | `VERIFY.md` | `reality-checker` + `code-reviewer` | `code-review-and-quality`, `verification-loop`, `agent-self-evaluation` | informe Go/No-Go con evidencia |

Cada fase se ejecuta **secuencialmente**. No se abre la siguiente hasta que la
anterior pase su compuerta. Un agente que encuentre un bloqueo debe aplicar el
fallback escrito en su contrato y dejar constancia; **no puede detenerse a
preguntar** salvo que el contrato lo autorice expresamente.

## 3. Reglas globales (aplican a todas las fases)

1. **Alcance mínimo**: solo los archivos listados en el contrato de la fase. No
   refactorizar módulos completos, no renombrar APIs públicas, no tocar el frontend
   salvo que el contrato lo pida.
2. **Sin dependencias nuevas** de Python ni de infraestructura. Usar lo que ya
   está en `requirements/`.
3. **Compatibilidad**: los tests existentes deben seguir pasando. Si un test
   existente contradice el plan, se actualiza el test **con justificación en el
   propio test** (comentario breve) y se reporta.
4. **Idioma**: comentarios y mensajes nuevos en español; identificadores en el
   idioma del archivo que se edita.
5. **Seguridad**: el contenido del expediente sigue siendo UNTRUSTED. No se
   relajan `html.escape`, allowlists de tools ni validación de citas.
6. **Evidencia**: cada fase deja su salida en `var/eval/` (ignorado por git) y
   un resumen en el mensaje final del agente con comandos ejecutados y resultados.
7. **Sin secretos**: no imprimir claves de `.env` ni tokens en logs o artefactos.
8. **Git**: no crear commits ni ramas. El responsable decide qué se commitea.
9. **Prohibido “arreglar” midiendo**: no se ajustan umbrales para que un caso
   pase; se corrige la causa. Cualquier cambio de umbral debe estar en el contrato.
10. **Fallback antes que bloqueo**: si una fuente no existe (p. ej. no hay
    transcripciones), el código debe degradar de forma explícita y testeada.

## 4. Mapa de propiedad de archivos

| Fase | Archivos que puede modificar |
|---|---|
| F0 | `evals/golden/chat_investigacion.yaml`, `scripts/eval_golden_chat.py`, `tests/unit/test_eval_golden_chat.py` |
| F1 | `apps/api/app/services/intent.py`, `apps/api/app/core/config.py`, `apps/api/app/routers/query.py` (solo selección de modelo), `tests/unit/test_intent.py`, `tests/unit/test_intent_corpus.py` (si aplica), `tests/unit/test_retrieval_plan.py` (solo si cambia el contrato), `.env`, `.env.example` |
| F2 | `apps/api/app/services/builtin_agents.py`, `apps/api/app/services/fact_seed.py` (nuevo), `apps/api/app/services/claim_seed.py` (solo reutilización), `apps/api/app/services/retrieval_plan.py`, `apps/api/app/services/retrieval_rank.py`, `apps/api/app/services/agent.py`, `apps/api/app/routers/chats.py`, `apps/api/app/routers/query.py` (solo emisión SSE), `tests/unit/test_fact_seed.py`, `tests/unit/test_retrieval_rank.py`, `tests/unit/test_builtin_chat.py` |
| F3 | `packages/prompts/answer_question.v2.md`, `packages/prompts/answer_question.v3.md`, `apps/api/app/services/answering.py`, `apps/api/app/services/compose.py`, `apps/api/app/services/agent.py` (solo retorno de markdown), `apps/api/app/routers/query.py` (solo paso de markdown), `tests/unit/test_compose.py`, `tests/unit/test_answering.py`, `tests/unit/test_answer_contract.py`, `tests/unit/test_compose_markdown.py` (nuevo) |
| F4 | `apps/api/app/services/evidence_meta.py` (nuevo), `apps/api/app/services/answering.py`, `apps/api/app/services/compose.py`, `apps/api/app/services/legal_guard.py`, `apps/api/app/routers/query.py`, `tests/unit/test_claim_revalidation.py`, `tests/unit/test_legal_guard_meta.py` (nuevo) |

Si un agente necesita tocar un archivo fuera de su mapa, debe justificarlo en el
mensaje final; el verificador lo revisará.

## 5. Criterios de “sin gaps”

Cada contrato enumera requisitos numerados (`R1`, `R2`, …), criterios de
aceptación (`A1`, `A2`, …) y pruebas (`T1`, `T2`, …). Un requisito sin prueba es
un gap. Un criterio sin comando reproducible es una opinión.

El verificador final (`VERIFY.md`) comprueba:

- todos los `R*` tienen implementación localizable;
- todos los `A*` tienen evidencia;
- todos los `T*` existen y pasan;
- no hay `TODO`, `FIXME`, `pass` nuevo, ni ramas `NotImplemented`;
- el chat real (API en Docker) responde las 3 preguntas del set dorado con la
  calidad mínima definida.

## 6. Definición de terminado global

1. `pytest tests/unit -q` en verde.
2. Tests de integración relevantes en verde (o skip justificado si el entorno no
   tiene la BD de test).
3. `ruff check` sin errores nuevos en los archivos tocados.
4. Contenedor `judicial-ai-api-1` reconstruido con los cambios y `/health` OK.
5. `scripts/eval_golden_chat.py --label after` produce un informe donde:
   - P1 cita el pagaré principal y los 15 pagarés de la demanda acumulada;
   - P2 distingue tope del mandamiento, capitalización probada y doble cobro;
   - P3 tiene estructura de informe, ≥8 citas y ≥2500 caracteres;
   - ninguna de las 3 abre con “Encontré material relacionado…” ni contiene
     “Se omitieron afirmaciones…”.
6. Informe `var/eval/golden_after.md` con comparación antes/después.
7. `VERIFY.md` firmado por el verificador con veredicto `GO`.
