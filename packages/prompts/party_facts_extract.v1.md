# Extraccion de hechos por parte

Extrae hechos candidatos del expediente. No declares nada probado.

Devuelve JSON:
```json
{
  "facts": [
    {
      "proposition": "hecho",
      "fact_type": "PAYMENT|CONTRACT|DATE|AMOUNT|CONDUCT|STATUS",
      "party_side": "DEMANDANTE|DEMANDADO|TERCERO|NO_ATRIBUIDO",
      "party_name": "nombre o null",
      "position": "ALLEGES|DENIES|ADMITS|UNKNOWN",
      "claim_ids": [],
      "citation_handles": ["E1"],
      "confidence": 0.0,
      "legal_issue": "tema"
    }
  ],
  "warnings": []
}
```
Cada hecho debe tener al menos una cita o un claim con cita. No inventes
admissions ni decisiones.
