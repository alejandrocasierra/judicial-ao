# Corpus de intención del chat

Mide el router (`apps/api/app/services/intent.py`) contra preguntas etiquetadas.
El router decide el formato y la tubería de cada turno; un error en `navegar` o
`agregacion` responde con una lista de menciones una pregunta sustantiva y gasta
0 LLM, así que esos falsos positivos son los caros.

## Archivos

| Archivo | Qué es |
|---|---|
| `corpus.csv` | corpus de trabajo (146 preguntas): reales + dorado + semilla |
| `corpus_real.csv` | 26 preguntas **reales** exportadas de la base (con sus erratas) |
| `corpus_seed.csv` | 78 preguntas semilla escritas por un agente |

Columnas: `question`, `label` (una de las 7), `needs_history` (`si` si solo se
entiende con el turno anterior), `has_attachments`, `suggested_label` /
`confidence` / `reason` (lo que decide el router hoy, informativo), `source`
(`real` \| `golden` \| `seed`) y `labeler` (`user` \| `human` \| `agent`).

**Procedencia importa.** `source=real` son preguntas que un usuario escribió en
la base de desarrollo; `golden` son casos del set dorado con etiqueta humana;
`seed` es una semilla escrita por un agente para fijar el arnés. El eval reporta
la accuracy de cada grupo por separado: la de `real` es la que no se puede
maquillar.

## Comandos

```powershell
# Exportar preguntas reales (deja `label` vacía para etiquetar a mano)
.\.venv\Scripts\python.exe scripts\export_intent_corpus.py --org-id <uuid> --days 3650

# Añadir las etiquetas verdaderas que dejaron los usuarios (intent_feedback)
.\.venv\Scripts\python.exe scripts\export_intent_corpus.py --org-id <uuid> --from-feedback --append

# Recalcular sugerencias tras cambiar el router (no toca `label`)
.\.venv\Scripts\python.exe scripts\export_intent_corpus.py --refresh

# Medir
.\.venv\Scripts\python.exe scripts\eval_intent.py --show-errors 20
```

## Resultado actual (router de reglas)

146 casos · **accuracy 93,8 %** · macro-F1 0,952 · **0 FP de `navegar`** · 0 FP de `agregacion`.

| Origen | Casos | Accuracy |
|---|---|---|
| `real` | 26 | 88,5 % |
| `golden` | 50 | 92,0 % |
| `seed` | 70 | 97,1 % |

Los 5 errores que quedan son 3 erratas reales (`juces`, `cuentos`, `nombredes`)
y 2 preguntas de análisis coloquial («hay algo raro entre…», «¿está respaldada
por evidencia?»). No se arreglan con más regex: los cubren el L2 y el perfil por
usuario, que es exactamente para lo que existen.

## Honestidad metodológica

- Las 26 preguntas reales las etiquetó un agente leyéndolas, no un abogado. Son
  mejores que la semilla porque la **distribución** es real (erratas incluidas),
  pero siguen necesitando revisión humana. Al revisarlas, cambia `labeler` a
  `human` y vuelve a medir.
- Los números están medidos sobre el mismo corpus que guió los ajustes de
  regex. No es un holdout limpio: sirve para detectar regresiones y para ver qué
  falta, no como promesa de producción. El holdout real es el veredicto de los
  usuarios (`intent_feedback`) y el set dorado.
- Cuando haya ≥ 150 preguntas reales etiquetadas por personas, `real` pasa a ser
  el número de referencia y la semilla se puede retirar del corpus de trabajo.
