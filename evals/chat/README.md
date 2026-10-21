# Banco de pruebas del chat

`scripts/eval_chat_quality.py` genera preguntas desde el propio expediente y las
ejecuta contra la API. No usa un juez LLM: mide señales duras y reproducibles.

## Qué mide

| Señal | Por qué importa |
|---|---|
| `answer_kind` | `insufficient` sobre una pregunta construida con datos que existen es un fallo de recuperación o de prompt |
| `citations` | una respuesta sin fuentes no es verificable |
| `refusal` | «No se encontró evidencia» cuando el tema sí está en el expediente |
| `degraded` | el turno se quedó sin presupuesto o el agente falló |
| `length` | una pregunta exhaustiva no puede caber en 120 palabras |
| `p50/p95` | latencia real por tipo de pregunta |

## Generación

- **entidades** (`person`, `organization`, `contract`): «¿Qué se dice sobre X?»
- **documentos**: «¿Qué contiene y qué afirma el documento X?»
- **contradicciones**: «Explique esta contradicción y las dos versiones» (descripción completa).
- **claims**: «¿Qué dice el expediente sobre este hecho y qué fuente lo respalda?».
- **temas**: pago de intereses, pagaré, interrogatorio, audiencia, contrato…
- **exhaustiva**: la pregunta original de la incidencia.

Con `--per-type 400` el banco pasa de mil preguntas (claims + entidades +
documentos + contradicciones). El límite es el coste y la latencia del
proveedor, no el generador. El runner reintenta 429/5xx con backoff y admite
`--concurrency 2-3`; el límite por usuario del API es
`RATE_LIMIT_QUERIES_PER_MINUTE` (30 por defecto, súbelo solo en dev).

## Uso

```powershell
# Humo: 20 preguntas
.\.venv\Scripts\python.exe scripts\eval_chat_quality.py --limit 20

# Solo contradicciones, contra otra API/modelo
.\.venv\Scripts\python.exe scripts\eval_chat_quality.py --api http://127.0.0.1:8000/v1 `
  --types contradiction --limit 20 --model-id <uuid>

# Banco grande (1.000) con el modelo rápido de Gemini
.\.venv\Scripts\python.exe scripts\eval_chat_quality.py --limit 1000 --per-type 400 `
  --concurrency 3 --model-id <uuid-lite> --out var/chat_eval/full1000.jsonl
```

Salida: `*.jsonl` (una fila por pregunta, con la respuesta recortada) y
`*.summary.json` (tasas y latencias por tipo).

## Resultado de la intervención (dev)

Antes: la pregunta exhaustiva devolvía `insufficient`, 0 citas, 71 caracteres;
«¿qué dice el expediente sobre la audiencia?» se abstenía en 0,1 s por un filtro
de términos; los seguimientos decían «no queda claro a qué “lo anterior” se
refiere».

Después:

- exhaustiva: `direct`, 6–10 citas, 2.900–3.600 caracteres, 48–60 s;
- audiencia: `direct`, 3 citas;
- documento nombrado: `direct`, 2 citas, 9 s;
- seguimiento: responde con el pagaré y el interrogatorio;
- lote de 21: temas 8/8, contradicciones 4/4, exhaustiva 1/1, entidades 3/4.

Los «documentos» del lote fallaban porque la pregunta pedía un tema que esos
documentos no tratan; el banco ahora pregunta por el contenido del documento y
la recuperación resuelve el nombre del archivo a `document_id`.

## Verificación contra la base (OCR/ASR/claims/grafo)

El banco anterior mide forma; `scripts/verify_chat_quality.py` mide **verdad**:

1. Cada `citation_id` se resuelve a su página OCR (`document_pages`) o segmento
   ASR (`transcript_segments`); si no existe o está vacío, la cita es inválida.
2. Cada afirmación se compara con el texto citado: solapamiento de términos y
   **números que no aparecen en la fuente** (la alucinación más grave).
3. Según el tipo: documento correcto, entidad en `entities`, claim respaldado,
   las dos versiones de una contradicción (`contradictions` + `citations`), o
   cobertura de contradicciones conocidas.
4. Re-ejecuta el retrieval y comprueba si la fuente esperada estaba en el top-k
   (separa «no recuperó» de «recuperó y el prompt falló»).
5. Opcional `--judge-model-id`: un modelo **distinto** al que respondió audita
   cada afirmación contra la evidencia citada (faithfulness/relevance).

```powershell
.\.venv\Scripts\python.exe scripts\verify_chat_quality.py `
  --input var/chat_eval/verify2.jsonl --judge-model-id <uuid-juez>
```

### Antes / después (muestras distintas, dirección)

| Métrica | Antes (35) | Después (20) |
|---|---|---|
| `insufficient_rate` | 14,3 % | 10,0 % |
| `citation_integrity` | 93,3 % | **100 %** |
| `claim_support` (claims vs fuente citada) | 69,0 % | **95,3 %** |
| filas con números fuera de la fuente | 22,9 % | **15,0 %** |
| `document_match` | 60,0 % | **100 %** |
| `entity_match` | 0 % | **100 %** |
| contradicción con las dos versiones | 33,3 % | **80,0 %** |
| `judge_faithful` (todas las claims) | 34,3 % | 60,0 % |
| `judge_answers` | 42,9 % | **95,0 %** |
| `judge_claim_support` | 71,4 % | 80,4 % |

Los cambios que produjeron la mejora: semilla de las dos afirmaciones de una
contradicción desde `claims` + `citations`, semilla de menciones literales de una
persona/entidad en OCR/ASR, ruteo de entidades al agente (`find_person`/grafo) y
reglas nuevas del prompt v3 (no afirmar ausencia, no elegir entre cifras
contradictorias, no inferir actos procesales).

**Lo que sigue fallando**: 6 de cada 10 respuestas tienen al menos una
afirmación que el juez no considera respaldada; 15 % de las filas meten algún
número que no está en la página citada; y la contradicción «título valor base»
sigue mezclando versiones. Es la lista de trabajo para el siguiente ajuste de
prompt/verificación, no un aprobado global.

