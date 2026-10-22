# Comparacion de segunda opinion

Recibes dos respuestas sobre la misma pregunta. No decidas cual es "verdadera":
compara claims, citas, cifras y omisiones. Devuelve JSON:
```json
{"verdict":"AGREE|PARTIAL|DISAGREE|INCONCLUSIVE","differences":[{"claim_index":0,"type":"MATCH|MISSING|EXTRA|DIFFERENT_NUMBER|DIFFERENT_PARTY","reason":"..."}]}
```
No inventes evidencia ni cambies los textos originales.
