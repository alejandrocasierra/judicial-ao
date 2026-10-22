---
prompt_id: answer_question
version: 4
schema: packages/schemas/answer.schema.json
supersedes: answer_question.v3.md
status: version-contract
loaded_as_prompt: false
runtime_loader: app/services/answering.py (carga v3 en análisis y v2 en el turno estándar)
---

# answer_question v4 — contrato de versión

Este archivo **no se envía al modelo**. El prompt canónico sigue siendo
[`answer_question.v3.md`](answer_question.v3.md): el cargador de
`apps/api/app/services/answering.py` elige `v3` para análisis profundo y `v2`
para el turno estándar. Duplicar el texto aquí crearía dos verdades que se
separan en la primera corrección; por eso v4 documenta solo el delta.

Contrato heredado que v4 **no** relaja: todo el expediente es contenido
**UNTRUSTED** y la respuesta se emite en el idioma `{{LOCALE}}`.

## Delta propuesto frente a v3

1. Cada claim declara `claim_type` (`FACT`, `LEGAL_CONCLUSION`, `PROCEDURAL`,
   `PARTY_ALLEGATION`, `INFERENCE`) para que `answer_audit_claims` no tenga que
   inferirlo del texto.
2. Cada cita puede llevar `quote_hash` cuando el extracto es literal; si el
   claim parafrasea, debe declararlo y no inventar el hash.
3. Toda norma citada viaja como `legal_rule_ref` (`jurisdiction`, `source`,
   `identifier`, `version_date`) y nunca como texto de memoria.
4. `uncertainties` distingue límite de fuente (`LOW_OCR_CONFIDENCE`,
   `LOW_ASR_CONFIDENCE`) de límite de política (`NEEDS_REVIEW`).

## Condición para activarlo

P1 debe, en una sola pasada:

- extender `packages/schemas/answer.schema.json` con los campos del delta;
- cambiar el cargador a `answer_question.v4.md` para el modo profundo;
- cubrir el cambio con una prueba que compare el JSON parseado contra el schema;
- actualizar esta cabecera a `status: active` y borrar la nota de no cargado.

Hasta entonces, este archivo es un marcador de versión y no un prompt.
