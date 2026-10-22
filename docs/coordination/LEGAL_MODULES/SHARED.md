# Coordinación: cierre de gaps de módulos legales

**Fecha:** 2026-10-22
**Base:** commit `0059960` + estado local con corpus de 38 normas por organización y una segunda opinión real ejecutada.

## Reglas de trabajo

1. Cada agente trabaja en el workspace actual y no hace `git commit` ni `git push`. El orquestador revisa e integra.
2. No tocar producción sin target, backup verificado y autorización explícita. Si faltan credenciales, dejar runbook y marcar bloqueado; no simular despliegue.
3. No inventar normas, artículos, cifras ni firmas legales. Para corpus, solo texto obtenido de fuente oficial y con checksum reproducible.
4. Respetar territorios:
   - **P0:** `data/legal_rules/**`, `scripts/build_legal_corpus.py`, `scripts/build_checksums.py`, `tests/e2e/**`, `evals/golden/legal*`, `docs/coordination/P0.md`, documentación de despliegue.
   - **P1:** `apps/api/app/services/legal_guard.py`, `second_opinion.py`, `party_facts.py`, routers/esquemas asociados, migraciones nuevas, `apps/web/src/**`, `tests/unit/test_legal_modules.py`, `tests/integration/test_legal_modules_api.py`, `docs/coordination/P1.md`.
   - **P2:** `docs/architecture/**`, `packages/prompts/**`, `packages/schemas/**`, `docs/*.md` (excepto los de P0), `apps/api/app/core/config.py` solo para flags, `docs/coordination/P2.md`.
5. Si un cambio cruza territorio, escribir una solicitud en `EVENTS.md` con `TO: P0|P1|P2` y continuar con lo propio. No editar el archivo del otro agente.
6. Cada agente debe leer este archivo y `EVENTS.md` antes de empezar, y actualizar su archivo de estado al terminar cada bloque.
7. Entregable final por agente: cambios, pruebas ejecutadas, evidencia, gaps que no pudo cerrar y bloqueos concretos. No declarar “completo” si falta una prueba o una firma.

## Contexto de la base local

- Organización Alfa: `b4e6d687-89b0-4b79-a0cc-69bd3532a7e9`
- Organización Beta: `9830ee40-091c-4f51-9454-638068a53139`
- Caso local: `38865959-8d5f-44e4-bbc8-1226bd345b00`
- Auditoría real: `6cbcd40c-8a52-4f1d-bd0c-8305d7f3b52d`
- Run segunda opinión: `65cc66b1-25e3-40f2-9e45-efc916ae24b6`
- Corpus actual: 38 normas por organización, 76 filas.
- Head Alembic: `0061_second_opinion_worker`.

## Criterio de cierre

Un punto está cerrado solo si: (a) hay cambio verificable, (b) hay prueba o evidencia reproducible, (c) no introduce una afirmación legal sin fuente, y (d) el gap residual está documentado con nombre y acción.
