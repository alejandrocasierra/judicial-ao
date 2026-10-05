# Conjunto dorado de evaluación

Formato JSONL, un caso por línea (ver `example.jsonl`). Los criterios de fallo siguen el §133 del SSD:

- La cita no existe.
- La fuente no existe.
- La cita apunta a un documento, página o timestamp equivocado.
- El claim se atribuye a una persona incorrecta.
- Una alegación se presenta como hecho.
- Se omite una contradicción relevante.

Pendiente (Sprint 5): `scripts/run_evals.py`. Ejecutará cada caso contra `/query` con el proveedor real y
reportará:

- `citation_validity`
- `answer_grounding`
- `contradiction_recall`
- `abstention_accuracy`
