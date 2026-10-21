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
4. Re-ejecuta el retrieval y mide si la fuente esperada estaba en el top-k
   (`rag_recall_proxy`). Es un **diagnóstico**, no una medida de corrección: un
   claim puede citar otra fuente válida y el juez decide. No se cuenta como fallo
   para preguntas de claim.
5. Opcional `--judge-model-id`: un modelo **distinto** al que respondió audita
   cada afirmación contra la evidencia citada (faithfulness/relevance).

```powershell
.\.venv\Scripts\python.exe scripts\verify_chat_quality.py `
  --input var/chat_eval/verify2.jsonl --judge-model-id <uuid-juez>
```

### Antes / después (muestras distintas, dirección)

| Métrica | Antes (35) | Después (20) |
|---|---|---|
| `insufficient_rate` | 14,3 % | 15,0 % |
| `citation_integrity` | 93,3 % | **100 %** |
| `claim_support` (claims vs fuente citada) | 69,0 % | **100 %** |
| filas con números fuera de la fuente | 22,9 % | **0 %** |
| `document_match` | 60,0 % | **100 %** |
| `entity_match` | 0 % | 75 %¹ |
| contradicción con las dos versiones | 33,3 % | 60 %² |
| `judge_faithful` (todas las claims) | 34,3 % | **95,0 %** |
| `judge_answers` | 42,9 % | **85,0 %** |
| `judge_claim_support` | 71,4 % | **97,8 %** |

¹ El 75 % era un falso negativo del verificador por el «(04)» del nombre; la
comparación por tokens ya lo corrige. ² Las dos contradicciones parciales de esa
muestra son de cobertura (falta una fuente en el expediente), no afirmaciones
inventadas; el `título valor` de los cuatro casos da 100 %.

### Cierre de los cuatro casos (misma corrida, mismo juez)

| Caso | Resultado |
|---|---|
| **Administraciones Judiciales Ltda.** (entidad) | 1 claim, 3 citas; el certificado incluye la gerente (p. 107). Sin “Tribunal” inventado. `supported`. |
| **Título valor base** (contradicción) | 6 claims, 2 citas; Versión A ($1.815.000.000), Versión B ($1.528.030.000), frase de contraste y el fragmento de la contestación **limpiado por el modelo** con la tasa del 1,35 % y $209.722.117. Todas `supported`. |
| **0013 AnexoContestación** (documento) | 1 claim, 1 cita; sin la página 51 que no estaba en la evidencia. `partial` pero fiel. |
| **Diligencia “se da por terminada”** (claim) | 1 claim, 1 cita al acta del 12 de mayo; ya no atribuye la frase a la grabación. `supported`. |

Resumen JSON de esa corrida (`var/chat_eval/fix4_final9.verify.summary.json`):

```json
{"n": 4, "insufficient_rate": 0.0, "citation_integrity": 1.0,
 "claim_support": 1.0, "unsupported_number_rows": 0.0,
 "document_match": 1.0, "entity_match": 1.0,
 "contradiction_both_versions": 1.0, "expected_source_cited": 1.0,
 "rag_recall_proxy": 1.0, "ocr_or_asr_review": 0.0,
 "judge_faithful": 1.0, "judge_answers": 1.0,
 "judge_claim_support": 1.0, "worst": []}
```

`ocr_or_asr_review` es una alerta de calidad de OCR/ASR, no una alucinación; en
esta corrida quedó en 0 %.

Los cambios que produjeron el cierre: resolver la cita al **texto primario** de
la página/segmento (no al chunk del agente), no renderizar una afirmación si el
presupuesto no alcanza para **todas** sus fuentes, validar cargos y nombres
propios (con alias de OCR), forzar las dos versiones de una contradicción
registrada, semilla literal/números para claims sin cita, eliminar límites meta
(«no se recuperó…») y juez síncrono para agente/claim.

### Memoria, caché, agentes y partes

- **Memoria entre sesiones**: al preparar un turno se añaden los últimos 2
  intercambios del mismo usuario y expediente en sesiones anteriores (máx. 4
  mensajes), además del historial de la sesión actual. Sirve para «lo anterior»
  sin arrastrar todo el historial.
- **Seguimiento por rol**: `query_rewrite` reconoce que «¿Qué dice la parte
  demandada?» es un seguimiento de «intereses» y busca con el tema anterior; no
  exige que el usuario lo repita. Probado en la misma sesión: la segunda
  respuesta habla de los intereses alegados por la defensa, no «no se encontró».
- **Etapas visibles**: el SSE manda agente + skill, «Investigando el
  expediente», «Revisando la evidencia», «Redactando la respuesta» y
  «Verificando citas, cifras y nombres»; el widget los muestra en secuencia.
- **Caché de respuestas**: una pregunta repetida del mismo usuario/expediente,
  modelo, agente, estrategia, versión de pipeline y versión de evidencia
  devuelve la respuesta completa (`cached: true`) sin retrieval ni LLM. TTL
  `ANSWER_CACHE_TTL_SECONDS` (24 h por defecto). Las preguntas dependientes del
  contexto incluyen el hash de los últimos turnos; con adjuntos no se cachea.
  Prueba local: 8,8 s → 0,3 s en la segunda llamada.
- **Agentes automáticos**: contradicciones/análisis usan **Cronista probatorio**
  (Cazador de contradicciones + Cronologista); el resto usa **Asistente del
  expediente** (Relacionador de personas + Analista de documento). Se cargan su
  prompt y skills con `skills.load_config`; si el usuario elige un agente, ese
  manda.
- **Partes del proceso**: las preguntas de rol/partes se responden con la tabla
  `parties` (juez, demandante, demandado, apoderados, terceros), no con menciones
  sueltas. «¿Quiénes son los demandados?» devuelve exactamente los 2 demandados;
  «¿Quiénes son las partes?» agrupa los 24 registros. La respuesta indica
  «Fuente: ficha de partes del proceso».

### Muestra amplia (20) con el mismo juez

`judge_faithful` 95 %, `unsupported_number_rows` 0 %, `claim_support` 100 %,
`citation_integrity` 100 %. Quedan 3 abstenciones (preguntas sin evidencia) y
dos contradicciones **parciales** por cobertura, no por afirmaciones inventadas;
`entity_match` marcaba 75 % por un `(04)` en el nombre, corregido en el
verificador con comparación por tokens.

