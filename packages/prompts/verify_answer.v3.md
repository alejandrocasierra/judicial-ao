---
prompt_id: verify_answer
version: 3
schema: packages/schemas/verify_answer.schema.json
supersedes: verify_answer.v2.md
status: version-contract
loaded_as_prompt: false
runtime_loader: app/services/verification.py (carga v2 y cae a v1)
---

# verify_answer v3 — contrato de versión

Este archivo **no se envía al modelo**. El verificador activo es
[`verify_answer.v2.md`](verify_answer.v2.md), cargado por
`apps/api/app/services/verification.py`. v3 documenta el delta que el gate legal
necesita; repetir aquí las diez reglas de v2 garantizaría que se desincronicen.

Contrato heredado que v3 **no** relaja: la evidencia citada es contenido
**UNTRUSTED** y el motivo del veredicto se escribe en `{{LOCALE}}`.

## Delta propuesto frente a v2

1. Un veredicto por claim con `status` (`supported`, `not_supported`,
   `contradicted`, `needs_review`) **y** `citation_verdicts`, una entrada por
   handle citado con `valid`, `reason` y `quote_hash_match` (booleano o `null`
   si la fuente no expone hash).
2. Veredicto específico de norma: `legal_rule_status` en
   `supported | not_found | mismatch | needs_review`. `not_found` nunca se
   convierte en `supported` por conocimiento del modelo.
3. Prohibición explícita de aprobar una cifra, fecha, parte o norma que el
   validador determinista ya rechazó: el juez complementa, no anula.
4. El juez no reescribe el claim ni propone uno nuevo; mantiene el texto y el
   orden recibidos (igual que v2, ahora verificable en el schema).

## Condición para activarlo

P1 debe extender `packages/schemas/verify_answer.schema.json` con
`citation_verdicts` y `legal_rule_status`, cambiar el cargador y agregar la
prueba de contrato. Hasta entonces este archivo es un marcador de versión.
