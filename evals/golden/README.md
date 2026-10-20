# Conjunto dorado de evaluación

## Archivos

- `chat_pipeline.yaml` — set principal del pipeline de chat (50 casos):
  12 `dato`, 8 `navegar`, 8 `análisis`, 6 `procedimental`, 6 `agregación`,
  5 sin evidencia y 5 adversariales. Cada caso declara `intent`,
  `expected_answer_kind`, `must_cite`, `must_not_cite`, `must_not_claim`,
  `max_visible_citations`, `latency_budget_ms` y `notes`.
- `pilot_manual.yaml` — 20 preguntas verificadas a mano sobre el expediente
  piloto (mismos `must_cite` de página/archivo).
- `example.jsonl` — formato histórico JSONL, extendido con `intent`,
  `expected_answer_kind`, `must_not_cite` y `max_visible_citations`.

Los casos con `case_ref` se ejecutan contra el expediente con ese número de
proceso. Los que no lo tienen usan el `--case-id` que reciba el runner.

## Capas

| Capa | Comando | Necesita |
|---|---|---|
| A — determinista (invariantes del compositor) | `python scripts/eval_offline.py` | nada |
| B — retrieval + compositor | `python scripts/run_evals.py --org-id … --user-id … --case-id … --llm fake` | PostgreSQL |
| C — juez semántico + matriz cross-model | `python scripts/eval_matrix.py --models … --judge …` | claves de proveedor (o `--offline`) |
| Latencia | `python scripts/bench_chat.py` | opcional PostgreSQL |
| Carga HTTP | `python scripts/load_chat.py --case-id …` | API viva |

Los informes se escriben en `var/` (ignorado por git). Las cassettes
(`--record-dir` / `--replay-dir`) se guardan en `evals/cassettes/` y no se
versionan porque contienen respuestas de expedientes reales.

## Criterios de fallo

- la cita no existe o apunta a página/minuto equivocado;
- se cita una fuente de `must_not_cite`;
- se afirma una frase de `must_not_claim`;
- `answer_kind` no coincide con el esperado;
- abstención esperada sin `answer_kind=insufficient` (o al revés);
- más citas visibles que `max_visible_citations`;
- p95 de `dato` > 8 s o de `navegar` > 800 ms.
