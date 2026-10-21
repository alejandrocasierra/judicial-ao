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
- **contradicciones**: «Explique esta contradicción y las dos versiones».
- **temas**: pago de intereses, pagaré, interrogatorio, audiencia, contrato…
- **exhaustiva**: la pregunta original de la incidencia.

Con `--per-type 100` el banco pasa de mil preguntas. El límite es el coste y la
latencia del proveedor, no el generador.

## Uso

```powershell
# Humo: 20 preguntas
.\.venv\Scripts\python.exe scripts\eval_chat_quality.py --limit 20

# Solo contradicciones, contra otra API/modelo
.\.venv\Scripts\python.exe scripts\eval_chat_quality.py --api http://127.0.0.1:8000/v1 `
  --types contradiction --limit 20 --model-id <uuid>

# Banco grande (ej. 1.000) con el modelo rápido
.\.venv\Scripts\python.exe scripts\eval_chat_quality.py --limit 1000 --per-type 100 `
  --model-id <uuid-lite> --out var/chat_eval/full.jsonl
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

## Lectura correcta

Una tasa de abstención alta en `entity` puede ser legítima (la entidad aparece
solo en una lista). Una abstención en `contradiction`, `topic` o `document` con
evidencia existente es un fallo. Revisa el JSONL antes de tocar el prompt.
