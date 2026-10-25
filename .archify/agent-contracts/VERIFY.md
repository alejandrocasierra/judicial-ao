# Verificación end-to-end — Go / No-Go

## Objetivo

Comprobar con evidencia que las fases 0–4 están completas, integradas y que el
chat real (API + Postgres + modelo) produce respuestas de investigación, no notas
de una cita. El verificador no implementa: audita, ejecuta y firma.

## Entradas obligatorias

- `.archify/implementation_plan.md`
- `.archify/agent-contracts/README.md`
- Contratos `F0.md` … `F4.md`
- `var/eval/golden_before.json` y `.md`
- Estado actual del repositorio y del contenedor `judicial-ai-api-1`

## Requisitos de verificación

### V1. Auditoría de requisitos

Para cada contrato, construir una tabla `R* → archivo/línea → prueba → estado`.
Un `R*` sin implementación localizable es **gap** y bloquea el `GO`.

### V2. Auditoría de código

- `grep` de `TODO`, `FIXME`, `NotImplemented`, `pass  #` nuevos en los archivos
  del mapa de propiedad.
- Revisar que no se relajaron:
  - `html.escape` en prompts;
  - validación de handles en `parse_and_validate`;
  - allowlist de tools;
  - regla de números (`missing_numbers` sigue bloqueando).
- Revisar que no hay dependencias nuevas en `requirements/`.
- Revisar que no se tocaron archivos fuera del mapa sin justificación.

### V3. Suite automatizada

Ejecutar y adjuntar salida:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe -m pytest tests/integration -q
.\.venv\Scripts\python.exe -m ruff check apps/api/app evals scripts
```

- `tests/unit`: 100 % en verde, 0 fallos, 0 errores.
- `tests/integration`: en verde o skips justificados por entorno (documentar
  cuáles y por qué). Si la BD de test (`judicial-ai-postgres-test`) está
  disponible, no se aceptan skips de los tests tocados.
- `ruff`: sin errores nuevos en los archivos del mapa.

### V4. Sincronización de agentes en BD

```powershell
docker exec judicial-ai-postgres-1 psql -U postgres -d judicial -c `
  "SELECT name, left(system_prompt, 400) FROM agents WHERE kind='chat' ORDER BY name;"
```

Comprobar que “Asistente del expediente” y “Cronista probatorio” contienen
`facetas`, `search_transcripts`, `search_facts` y `Síntesis`.

### V5. Reconstrucción y salud

```powershell
docker compose build api
docker compose up -d --no-deps api
docker inspect --format='{{.State.Health.Status}}' judicial-ai-api-1
Invoke-RestMethod http://127.0.0.1:8000/health
```

- El build termina sin error.
- `/health` responde `{"status":"ok"}`.
- Revisar `docker logs --tail 200 judicial-ai-api-1` y confirmar que no hay
  `Traceback` ni `ERROR` nuevos al arrancar.

### V6. Set dorado después

```powershell
.\.venv\Scripts\python.exe scripts\eval_golden_chat.py `
  --api http://127.0.0.1:8000/v1 --label after `
  --out var/eval/golden_after.json
```

Criterios mínimos:

| Caso | Criterio | Umbral |
|---|---|---|
| INV-01 | `answer_kind` | `direct` o `partial` |
| INV-01 | citas | ≥ 3 |
| INV-01 | contenido | “15”, “77391469”, “78213828” |
| INV-01 | fuentes | 0002 y/o 0009 |
| INV-02 | `answer_kind` | `direct` |
| INV-02 | citas | ≥ 4 |
| INV-02 | contenido | “usura”, “886”, “capitaliz” |
| INV-02 | fuentes | 0009 o 0083 o 0077 o 0001Confirma (cualquier fuente primaria que sostenga el debate) |
| INV-03 | `answer_kind` | `direct` |
| INV-03 | citas | ≥ 8 |
| INV-03 | longitud | ≥ 2500 caracteres |
| INV-03 | estructura | ≥ 4 de: Síntesis, Demandante, Demandada, Interrogatorio, Decisión, Fuentes |
| INV-03 | contenido | “15 pagarés”, “2,5”, “1,6”, “886”, “Rojas Melo” |
| Las 3 | frases prohibidas | 0 apariciones de “Encontré material relacionado” y “Se omitieron afirmaciones” |
| Las 3 | latencia | ≤ 120 s por caso |

### V7. Inspección cualitativa (obligatoria)

Leer las 3 respuestas completas y responder por escrito:

1. ¿Abre con una respuesta/síntesis o con una disculpa? (debe ser lo primero).
2. ¿Distingue alegación, testimonio, prueba y decisión judicial?
3. ¿La posición de cada parte está atribuida y citada?
4. ¿Los minutos de audiencia incluyen hablante y `mm:ss`?
5. ¿La conclusión sobre usura separa el plano nominal (mandamiento) del material
   (capitalización/doble cobro)?
6. ¿Hay citas de relleno o irrelevantes? (marcar cuántas).
7. ¿Alguna cifra/fecha/nombre aparece sin respaldo? (debe ser 0).

Cada respuesta se puntúa 0–2 en: profundidad, estructura, trazabilidad,
atribución y honestidad. El `GO` exige ≥ 8/10 en INV-03 y ≥ 7/10 en INV-01/02.

### V8. Comparación antes/después

Generar `var/eval/golden_compare.md` con:

- `answer_kind`, citas, longitud, latencia y criterios PASS/FAIL antes/después;
- las frases prohibidas antes/después;
- una conclusión de 5 líneas: qué mejoró, qué no, y qué riesgo queda.

### V9. Regresión funcional

- Ejecutar 2 casos existentes de `evals/golden/chat_pipeline.yaml` (uno `dato`,
  uno `analisis`) con `scripts/run_evals.py --retrieval-only` o con el runner que
  ya exista, y confirmar que no bajan de sus umbrales.
- Confirmar que una pregunta de identidad (“¿Quién es Jorge Rojas?”) sigue
  respondiendo con la ficha/evidencia y no con un análisis largo innecesario.
- Confirmar que una pregunta de navegación (“¿En qué página aparece el oficio
  2834?”) sigue devolviendo `navigational`, no un informe.

### V10. Seguridad y datos

- Confirmar que las respuestas no incluyen el system prompt, claves ni rutas
  internas.
- Confirmar que un intento de inyección (“Ignora tus instrucciones…”) sigue
  absteniéndose o ignorando la instrucción.
- Confirmar que no se agregó ningún `print` con contenido del expediente ni con
  secretos.

## Veredicto

El verificador debe emitir:

```markdown
# Veredicto — <fecha>

GO | NO-GO

## Evidencia
- unit: ...
- integration: ...
- ruff: ...
- docker: ...
- golden before: ...
- golden after: ...
- qualitative: ...

## Gaps
| ID | Contrato | Requisito | Evidencia faltante | Acción |
|---|---|---|---|---|

## Riesgos residuales
- ...
```

`GO` solo si:

1. V1–V5 pasan;
2. V6 cumple **todos** los umbrales;
3. V7 da las puntuaciones mínimas;
4. V8 existe y no oculta regresiones;
5. V9 y V10 pasan;
6. no hay gaps abiertos.

Si algo falla, el verificador devuelve `NO-GO` con la lista exacta de acciones y
**no** autoriza cerrar la fase.

## Artefactos

- `var/eval/golden_after.json` / `.md`
- `var/eval/golden_compare.md`
- `var/eval/verification_report.md`
- salida de pytest y ruff (pegada en el informe)
- `docker inspect` y `/health`
- consulta a `agents`

## Prohibiciones

- No “pasar” un criterio cambiando el YAML o el umbral.
- No declarar GO con un caso en `timeout` o `error`.
- No aceptar “funciona en unitarios” si el chat real en Docker no lo demuestra.
- No editar código durante la verificación salvo para corregir un gap; si se
  corrige, se vuelve a ejecutar V3–V7 completos.
