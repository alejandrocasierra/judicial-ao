# OCR de manuscrito: corpus y fine-tuning

Cómo entrenar un OCR de línea para la **letra real** del expediente y conectarlo al pipeline.

## Por qué

RapidOCR/PP-OCR y Tesseract leen bien el texto **impreso** pero no la letra **manuscrita**
(los expedientes mezclan los dos). Se evaluaron modelos públicos:

| modelo | resultado en 17 páginas difíciles del caso 0001 |
|---|---|
| `microsoft/trocr-small-handwritten` | empeora y mete inglés (0.174→0.140 palabras es; dígitos perdidos) |
| `microsoft/trocr-base-handwritten` | no mejora ninguna línea |
| `ifesther/trocr-spanish-handwritten` | mejora en algunas pág. (0.38→0.50 en pág. 55) pero empeora en otras (0.23→0.11 en pág. 9), mete símbolos y tarda ~54 s/página en CPU |

Conclusión: ningún modelo público es fiable para manuscrito judicial colombiano. La vía es
**afinar un modelo con las correcciones humanas** del propio expediente.

## Flujo

### 1. Corregir páginas en el visor

Al guardar una corrección en el visor OCR, la página queda con `human_corrected = true`
(y `corrected_at`, `corrected_by`). Un reproceso automático ya **no** la pisa.
Se recomienda corregir, como mínimo, unas 100–200 páginas (≥500 líneas) para un modelo útil.

### 2. Construir el corpus (línea → texto)

```bash
.venv\Scripts\python scripts\build_handwriting_dataset.py \
    --org-id <ORG> --user-id <USER> --case-id <CASE> --out var/handwriting_dataset
```

Genera `images/*.png` (recortes de línea) y `metadata.jsonl` (`{"image", "text", ...}`).
La alineación entre las líneas que detecta el OCR y las líneas del texto corregido es
monótona y tolerante a errores (`app/services/handwriting_dataset.py`).

### 3. Afinar

```bash
# validación rápida (1 época, pocos ejemplos, CPU)
.venv\Scripts\python scripts\finetune_handwriting.py --dataset var/handwriting_dataset --smoke

# entrenamiento real (GPU recomendada)
.venv\Scripts\python scripts\finetune_handwriting.py \
    --dataset var/handwriting_dataset --base-model microsoft/trocr-base-handwritten \
    --output var/handwriting_model --epochs 20 --batch-size 8
```

Reporta CER/WER por época (`app/services/ocr_metrics.py`). Guarda el modelo y el procesador
en `--output`. Requiere `transformers`, `torch` y `sentencepiece` (fijados en `requirements`).

### 4. Usarlo en el pipeline

```
OCR_PROVIDER=handwriting
OCR_HANDWRITING_MODEL=var/handwriting_model
```

`HandwritingOCR` (`app/providers/ocr.py`) detecta líneas con RapidOCR y re-reconoce con el
modelo **solo** las líneas de baja confianza (`services/handwriting_ocr.py`). Si el modelo o
las dependencias no están disponibles, el pipeline sigue con el OCR impreso.

## Límites

- El modelo entrenado sólo conoce la letra/sesgo de quien etiquetó; conviene mezclar
  documentos de varias personas y aplicar aumento de datos.
- El corpus actual arranca con una sola página corregida (24 líneas): es una **muestra**,
  no suficiente para entrenar.
- En CPU el entrenamiento y la inferencia son lentos; usar GPU.
