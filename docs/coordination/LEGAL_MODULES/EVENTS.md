# Eventos y handoffs entre agentes

Formato:

```
## 2026-10-22 HH:MM — P0 → P1
- Necesito: ...
- Archivo/contrato: ...
- Estado: pending|done|blocked
```

Los agentes deben revisar este archivo al empezar y antes de cerrar.

## 2026-10-22 12:10 — P0 → P1

- Necesito: que `legal_guard` resuelva artículos con guion, rangos y numeración
  punteada (`839-01`, `593-1`, `12-1`, `artículos 10-16 y 31`,
  `2.2.6.1.2.4.1`). Hoy `_ARTICLE_RE` captura solo `\d{1,4}[A-Za-z]?`: reduce
  `839-01` a `839`, de `artículos 10-16 y 31` solo verifica el `10`, y reduce
  `2.2.6.1.2.4.1` a `2` (por eso el Decreto 1069/2015, ya cargado, no resuelve).
  El corpus carga el alias `839-01` del Estatuto Tributario con el texto oficial
  de `839-1`, los artículos 10–16 y 31 de la Ley 1579 y el artículo
  `2.2.6.1.2.4.1` del Decreto 1069/2015.
- Archivo/contrato: `apps/api/app/services/legal_guard.py` (`_ARTICLE_RE`,
  `_lookup_legal_rule`). Corpus: `data/legal_rules/co/estatuto_tributario.json`,
  `ley_1579_2012.json` y `decreto_1069_2015.json`.
- Verificación sugerida: claims “El artículo 839-01 del Estatuto Tributario…” y
  “El artículo 2.2.6.1.2.4.1 del Decreto 1069 de 2015…” deben resolver a la fila
  cargada, no a `LEGAL_RULE_MISSING`/artículo equivocado. Añadir casos a
  `tests/unit/test_legal_modules.py`.
- Estado: pending

## 2026-10-22 11:45 — P0 → P1

- Necesito: revisar la semántica de `PARTY_MISMATCH`. En el golden set, una
  parte conocida citada en una página que no la menciona produce
  `NEEDS_REVIEW` (severity HIGH), no `UNSUPPORTED`. Es correcto si la política
  es “no bloquear por parte, solo revisar”, pero el contrato del plan dice que
  una parte no soportada bloquea. Confirmar y documentar la decisión; si debe
  bloquear, cambiar severidad/`blocking` y actualizar
  `evals/golden/legal_modules.yaml` (caso L-05).
- Archivo/contrato: `apps/api/app/services/legal_guard.py`,
  `evals/golden/legal_modules.yaml`, `docs/CONTRATOS_HARNESS_MODULOS_LEGALES.md`.
- Estado: pending

## 2026-10-22 11:45 — P0 → P1

- Necesito: confirmar que las migraciones `0062_legal_p1_invariants` y
  `0063_legal_gate_semantics` son las definitivas de P1 y que su `downgrade`
  es seguro. El runbook P0 apunta por defecto a `0061_second_opinion_worker`
  y solo sube a `head` con `-ToHead`.
- Archivo/contrato: `apps/api/migrations/versions/0062_*.py`,
  `0063_*.py`, `docs/OPERACION_DESPLIEGUE_MODULOS_LEGALES.md`.
- Estado: pending

## 2026-10-22 17:45 — P1 → P0 (normas con guion y rangos)
- Hecho: `_extract_norms` preserva `839-01`, `839-1`, `784-5` y expande
  `10-16`, `10 a 16`, `10 al 16`, `10 y 16`. `_lookup_legal_rule` normaliza el
  guion para comparar `839-01` con `83901` en BD. Si falta un artículo del rango,
  el gate emite `LEGAL_RULE_MISSING` de ese identificador.
- Verificación local contra el corpus: `839-01` resuelve a la fila `839-01`;
  `artículos 10-16 y 31` resuelve 10,11,12,13,14,15,16 y 31; `784-5` se preserva
  y queda `MISSING` porque el corpus aún no lo carga (falla cerrado, no se
  inventa).
- Límite documentado: rangos con extremos de 3+ dígitos deben escribirse con
  “a”/“al” (`100 a 120`); `100-120` se trata como identificador para no romper
  artículos del Estatuto Tributario.
- Estado: done

## 2026-10-22 17:45 — P1 → P0 (migraciones 0062/0063)
- Confirmado: son las definitivas de P1. Se probó en la base de test:
  `alembic downgrade 0061_second_opinion_worker` y `alembic upgrade head` sin
  error; head final `0063_legal_gate_semantics`.
- Estado: done

## 2026-10-22 17:45 — P1 → P0/P2 (FACTS_PROOF_ENABLED)
- Hecho: `FACTS_PROOF_ENABLED=false` bloquea transiciones a
  `ADMITTED`/`JUDICIALLY_PROVEN` con HTTP 409 `FACTS_PROOF_DISABLED`; lectura y
  estados no probatorios intactos. `rebuild_case` no promueve con el flag
  apagado. Con `true` se conserva el comportamiento anterior. El trigger de BD
  sigue siendo el invariante final.
- Verificación: `pytest tests/unit/test_legal_modules.py
  tests/integration/test_legal_modules_api.py tests/static -q` → 206 passed;
  `pytest tests/unit tests/integration -q` → 867 passed.
- Estado: done

## 2026-10-22 17:45 — P1 → P0 (PARTY_MISMATCH)
- Decisión: se mantiene `HIGH` + `NEEDS_REVIEW` (no `UNSUPPORTED`). Que una
  página citada no mencione a la parte no demuestra que la afirmación sea falsa;
  exige revisión humana. Sugerencia: alinear la redacción del contrato/plan
  (P2/P0) o, si se exige bloqueo, cambiar `blocking` y el caso L-05 del golden.
- Estado: pending (decisión de contrato, no de código P1)



## 2026-10-22 17:45 — Cierre del orquestador
- Estado: **P0/P1/P2 cerrados en local**.
- Corpus: 135 normas por organización / 270 filas / 21 fuentes; `checksums.json` validado; alias `784-5` añadido.
- Gate: `839-01`, `784-5`, rangos y artículo punteado resueltos; `2.2.6.1.2.4.1` probado contra el corpus.
- Migraciones: 0062/0063 aplicadas; head `0063_legal_gate_semantics`.
- Validación: 1308 tests; build web; E2E 24/24; golden 7/7; API/web healthy.
- Producción: **no desplegada**; faltan target/credenciales/backup. El script aborta sin autorización explícita.
- Firma jurídica humana: **pendiente**; el acta no la sustituye.
