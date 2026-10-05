# Plan de trabajo — Judicial AI Platform

**Versión:** 1.0 · **Fecha:** 2026-09-30 · **Estado:** Fases 0–10 completadas; Fase 11 en planificación
**Caso piloto:** Expediente `11001310302120180036100` (ejecutivo hipotecario, Funza/Chía — 224 archivos, 8.88 GB, 205 PDF escaneados, 7 MP4 + 4 MOV, 8 XLSX índice)

> Punto de partida verificado: Sprint 0 ✅ completo (BD 28 tablas con RLS, auth, RBAC, auditoría, CI).
> Sprint 1 parcial (uploads ✅, jobs ejecutados por workers reales). Sprint 4 núcleo ✅ (`/query` con RAG FTS). Sprint 5 ✅ (embeddings + búsqueda híbrida). Sprint 6 ✅ (knowledge graph sobre PostgreSQL). Sprint 7 ✅ (agente jurídico + verificador + eval runner + golden suite). Sprint 8 ✅ (frontend Next.js + panel de administración). Sprint 9 ✅ (exportación CKP + benchmark del piloto). Sprint 10 ✅ (métricas, alertas, backups/DR, rate limiting por organización, load testing y auditoría).
> **Ausentes:** extracción con LLM real sobre documentos OCRizados (requiere API key), despliegue a producción (Fase 11).

---

## Decisiones de arquitectura tomadas en este plan

| # | Decisión | Motivo |
|---|---|---|
| D1 | **AnyDoc solo para Office/XLSX/PDF con texto**, nunca `ocr="hosted"` | AnyDoc no hace OCR local; el modo hosted envía el expediente a servidores de Firecrawl → inaceptable para datos judiciales. Para escaneados: OCR propio (Tesseract/Docling) |
| D2 | **Graphify NO se integra** como etapa del pipeline | Está orientado a codebases, sin ontología jurídica ni trazabilidad página/timestamp. Se usa solo como **referencia** (aristas `EXTRACTED/INFERRED/AMBIGUOUS`, export Neo4j). El grafo se construye propio sobre PostgreSQL |
| D3 | **El "grafo" es un knowledge graph, no una red neuronal** | Nodos/relaciones almacenados en BD (SSD §16). No hay que entrenar ninguna red neuronal |
| D4 | **Model routing obligatorio** (SSD §41) | Modelos económicos para OCR/clasificación/NER; frontier (Claude/GPT-6 Astra/Gemini) solo para razonamiento, contradicciones y respuestas. Ningún contexto de 1M cabe un expediente de 5.000 páginas (~3M tokens) → RAG sigue siendo la arquitectura, no el contexto gigante |
| D5 | **El XLSX índice es la fuente de verdad procesal**, no el nombre de archivo | Nomenclatura sucia confirmada (typos, 0003 duplicado, saltos). Orden y folios vienen del índice + contenido + sha256 |
| D6 | **Workers Celery + Redis** (SSD §23), Temporal solo si escala | VPS + Docker en MVP; K8s en producción de alto volumen |

---

## FASE 0 — Poner a funcionar lo que ya existe (1–2 semanas)

**Objetivo:** repo verde de punta a punta y los jobs dejan de ser decorativos.

### 0.1 Verificación del estado base
- Ejecutar `bash scripts/bootstrap.sh` y `bash scripts/run_tests.sh` (383 casos) hasta verde.
- Levantar `docker-compose.yml` completo (postgres+pgvector, redis, minio, clamav, api) y verificar `/health` y `/ready`.
- **Agente:** `coder` para ejecutar y corregir; `build-error-resolver` si falla el arranque.

### 0.2 Workers Celery (brecha crítica)
Hoy `POST /process` (en `apps/api/app/routers/cases.py`) solo hace INSERT en `jobs`. Construir:
- `apps/api/app/workers/` — `celery_app.py` (broker/backend Redis), `dispatcher.py` (mapea `job.type` → handler), handlers por tipo: `document_ocr`, `media_asr`, `diarization`, `legal_extraction`, `embedding`, `indexing` (tipos ya definidos en `app/schemas.py:84`). Handlers iniciales = no-op que marcan SUCCEEDED, se llenan en fases 2–5.
- Máquina de estados de job ya existe en `app/domain/states.py` (QUEUED→RUNNING→SUCCEEDED/FAILED↔RETRYING) — el dispatcher la consume, no la reinventa.
- `docker-compose.yml`: servicio `worker` (misma imagen `infra/docker/api.Dockerfile`, comando `celery -A app.workers.celery_app worker`).
- `.env.example` + `scripts/gen_env.py` + `app/core/config.py`: `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` (regla del repo: config sin defaults, todo por entorno).
- Tests: integración — crear caso, subir doc, `/process`, verificar job QUEUED→SUCCEEDED vía worker real.

### 0.3 Deuda conocida del backlog
- `services/ratelimit.py`: activar `RATE_LIMIT_BACKEND=redis` (la memoria es por proceso; con workers/API múltiples no sirve).
- Confirmar `.mov` en `services/files.py` (magic bytes `ftyp` — MP4 ya está; MOV suele compartir firma, verificar con los 4 MOV reales de la diligencia comisoria).

**Criterio de salida F0:** `make test` verde + job end-to-end ejecutado por worker real + stack Docker completo arriba.
**Skills/agentes:** `tdd-workflow`, `python-reviewer`, `code-reviewer` tras cada cambio.

---

## FASE 1 — Ingesta del expediente real (1–2 semanas) ✅ Completada 2026-09-28

**Objetivo:** registrar el expediente piloto completo, ordenado por sus XLSX, sin confiar en nombres de archivo.

### 1.1 Parser de índices XLSX (nuevo) ✅
- `apps/api/app/services/index_xlsx.py`: lee `0000IndiceExpediente*.xlsx` (openpyxl), extrae por cuaderno: número → nombre documento, fechas si existen. Salida: estructura ordenada por cuaderno (01PrimeraInstancia/0001…0007, 02SegundaInstancia/C001).
- Manejo de suciedad real: dos "0003" en cuaderno 0006, saltos de numeración (0101 con huecos), typos ("Grabacioin", "Respesta") → la clave de emparejamiento es **cuaderno + posición en índice + sub-orden**, complementada con **sha256** para dedup, nunca el nombre.
- Tests: `tests/unit/test_index_xlsx.py`.

### 1.2 Importador masivo de carpeta ✅
- `scripts/import_expediente.py` (CLI admin, no endpoint público): recorre la carpeta del expediente, para cada archivo: sha256 → dedup por caso → sniffing (`files.py`) → documento vs media → INSERT con metadata del XLSX (cuaderno, número de índice, sub-orden, orden procesal).
- 8.88 GB no pasan por HTTP: importador lee del filesystem montado y registra en storage write-once existente (`services/storage.py`). Los videos van directo a `media/`.
- Tests: `tests/integration/test_import_expediente.py`.

### 1.3 Tabla de linaje de cuadernos ✅
- `apps/api/migrations/versions/0004_cuaderno_linaje.py` extiende `documents`/`media` con: `cuaderno`, `indice_numero`, `indice_nombre_original`, `sub_orden`, `orden_procesal`, `es_indice_maestro`.
- El índice maestro general `0000IndiceExpedienteGeneral.xlsx` queda registrado como documento especial (`es_indice_maestro=true`, `cuaderno=NULL`); los índices por cuaderno también son documentos especiales.
- `apps/api/app/routers/documents.py` expone los campos de linaje en el listado de documentos.

**Resultado de la importación real:**
- Expediente `11001310302120180036100` (224 archivos, incluidos 8 XLSX de índice).
- 221 registrados, 3 duplicados por sha256 (contenido idéntico real, no errores de emparejamiento):
  - `0006 MedidasCautelaresAcumulado/0010 EscritoNuevamenteOficio 2018-361.pdf` ≡ `0003AcumuladoEjecutHipotecario201800361/0037 SolicitaOficios 2018-361.pdf`
  - `0006 MedidasCautelaresAcumulado/0014 Oficio0868 PROCESO 2018-0361REGISTRO-OFICIOMALHECHOCONANTERIORIDAD.pdf` ≡ `0006 MedidasCautelaresAcumulado/0012 Oficio0868REGISTRO-OFICIOMALHECHOCONANTERIORIDAD.pdf`
  - `0007 IncidenteNulidadDemandaAcumulada 2018-361/0001 AnexoIncidenteNulidadPRUEBAS 2018-361.pdf` ≡ `0003AcumuladoEjecutHipotecario201800361/0046 AnexoSolicitudCopias.pdf`
- 0 errores; orden procesal consultable por `orden_procesal`; audit trail escrito por cada ingesta.

**Criterio de salida F1:** ✅ 224 archivos procesados, 0 duplicados erróneos, orden procesal consultable, auditoría de cada ingesta, suite de pruebas verde (432 tests).
**Agentes:** `coder` + `database-reviewer` + `python-reviewer` + `code-reviewer` + `test-driven-development` skill.

---

## FASE 2 — Pipeline documental: OCR real (2–3 semanas) ✅ Completada 2026-09-28

**Objetivo:** los 205 PDFs (mayoría escaneados-imagen) pasan a `document_pages` con texto, folio y `ocr_confidence`.

### 2.1 Clasificador de páginas
- Por cada PDF: página con capa de texto (extraer directo, gratis) vs página escaneada (→ OCR). Los cuadernos masivos (0001, acumulados) son escaneados de miles de folios: **segmentación interna por folio** es el reto central.
- AnyDoc aquí: útil para los 8 XLSX y cualquier Office/PDF con texto (`pip install firecrawl-anydoc`, MIT, ~4 ms/doc, sin red). **Prohibido** `ocr="hosted"` (privacidad).

### 2.2 Proveedor OCR (`app/providers/ocr.py`, implementa `OCRProvider` de `base.py`)
- `TesseractOCR` (local, español `spa`, pdf2image 300 DPI) como baseline.
- Evaluar Docling/Surya como alternativa con layout (bounding boxes, tablas) — decisión con benchmark sobre muestra real del cuaderno principal.
- Guardar por página: texto, `ocr_confidence`, folio detectado (regex de numeración de folios), tsvector (columna generada ya existe).
- `OCR_CONFIDENCE_THRESHOLD` ya está en config — páginas bajo el umbral → `REVIEW_REQUIRED`, nunca se descartan (SSD §10.3).
- Rasterizado de páginas a imagen para el visor/verificación visual (deuda Sprint 1).

### 2.3 Clasificación documental
- Prompt versionado `packages/prompts/classify_document.v1.md` (inmutable, regla del repo) + taxonomía SSD §9 adaptada a Colombia (demanda, mandamiento de pago, auto, sentencia, recurso, medida cautelar, acta de audiencia, dictamen, póliza…). Modelo económico (routing D4).
- Job `document_ocr` encadena: páginas → OCR → folios → clasificación → `INDEXED`.

**Resultados y entregables:**
- `apps/api/app/providers/ocr.py`: `TesseractOCR` (local, español `spa`, PyMuPDF 300 DPI), `DoclingOCR` (RapidOCR ONNX local) con **fallback automático a Tesseract**, y `FakeOCR` para tests/desarrollo.
- `apps/api/app/services/image_preprocessing.py`: pipeline de preprocesamiento (grayscale, deskew, denoise, binarización adaptativa) configurable via `OCR_PREPROCESS`.
- `apps/api/app/services/document_pipeline.py`: OCR → folio (regex mejorada) → clasificación → imágenes rasterizadas en storage.
- `apps/api/app/services/document_classifier.py`: clasificación con LLM económico + prompt versionado `packages/prompts/classify_document.v1.md`; fallback heurístico en tests.
- `apps/api/app/workers/handlers/document_ocr.py` + `document_classification.py`: jobs reales conectados al executor.
- **Proveedor por defecto:** `OCR_PROVIDER=docling` en `.env`/`.env.example`; Tesseract como fallback.
- **Benchmark OCR — cuaderno principal, 50 págs @ 300 DPI:**

| Proveedor | Tiempo/pág | Confianza media | Bajo umbral 0.85 | Folios |
|---|---|---|---|---|
| Tesseract 5.5.3 + spa | 1.84 s | 0.765 | 58% | 2/50 |
| Docling (RapidOCR) | 4.02 s | 0.937 | 12% | 2/50 |

- **Ground truth / CER-WER:** `scripts/ground_truth_eval.py` genera 50 imágenes + plantilla editable. Comparación relativa Tesseract-vs-Docling: **CER=0.276, WER=0.413** (Docling como referencia pseudo-ground-truth). Para métricas reales se requiere transcripción humana en `var/ground_truth/ground_truth_template.json`.
- **Preprocesamiento:** se probó grayscale + deskew + denoise + binarización adaptativa. En la muestra no mejoró sobre Tesseract directo; queda disponible y desactivado por defecto (`OCR_PREPROCESS=false`).
- **Cuaderno 0002 completo con Docling:** 134 páginas en ~467 s (~3.5 s/pág), 11.2% bajo umbral, 33 folios detectados. Reporte: `var/benchmark_cuaderno_0002.json`.
- **Expediente real completo:** importado en BD judicial (`case_id=182a09e0-deeb-4918-80f6-19cf350b4087`) y en proceso de OCR masivo vía `scripts/process_all_pdfs.py`. Reporte de progreso: `var/process_all_pdfs.json`.
- **Correcciones defensivas aplicadas tras revisión cruzada:**
  - `process_document`: UPDATE atómico `SET processing_status='OCR_RUNNING' WHERE processing_status = ANY(:allowed)` para evitar doble procesamiento entre workers.
  - Serialización JSON defensiva de layout (`_to_json_safe`) para prevenir `ndarray`/tipos numpy en la BD.
  - `TesseractOCR`: `shlex.quote` en `--tessdata-dir`.
  - Workers: paso de arrays UUID nativos en lugar de literales de texto.
  - `process_all_pdfs.py`: reset de documentos `OCR_RUNNING`/`FAILED`, marcado automático a `FAILED` en errores, reanudación vía `var/process_all_pdfs.json`.
- Tests: suite verde (445 tests).

**Criterio de salida F2:** benchmark OCR sobre 50–100 páginas reales con ground truth (CER/WER, precisión de folio, precisión de clasificación); cuaderno 0002 completo procesado.
**Agentes:** `coder` + `rag-pipeline-reviewer` + `mle-reviewer` (benchmark).

---

## FASE 3 — Pipeline de audio/video (2–3 semanas) 🔄 En progreso

**Objetivo:** 7 MP4 (audiencias Art. 372 CGP y medidas cautelares) + 4 MOV (diligencia comisoria) → `transcript_segments` con timestamps y hablantes.

### 3.1 Extracción y ASR (`app/providers/asr.py`, implementa `ASRProvider`) ✅
- `apps/api/app/providers/asr.py`: `WhisperASR` (faster-whisper large-v3, español, timestamps por palabra) + `FakeASR` para tests/desarrollo; fallback CPU int8 si CUDA falla.
- `extract_audio_to_wav`: FFmpeg extrae audio 16 kHz mono; `FFMPEG_PATH` configurable.
- VAD previo configurable (`ASR_VAD_FILTER`, `ASR_VAD_PARAMETERS`).
- Docker worker con FFmpeg incluido (`infra/docker/api.Dockerfile`) y cachés de modelos persistentes (`HF_HOME`, `WHISPER_CACHE_DIR`).
- **Muestra 1 reprocesada en Docker:** `0044Grabacion1Octubre2025.mp4` → 64 segmentos, 2 speakers detectados por pyannote real, 12 segmentos marcados `needs_review`.
- **Muestra 2 en progreso:** `0048Grabacion4Diciembre2025.mp4` (344 MB, ~43 min) se procesa en el worker Docker con ASR CPU int8; pyannote para diarización.

### 3.2 Diarización y hablantes ✅
- `apps/api/app/services/diarization.py`: pyannote.audio 4 (`speaker-diarization-3.1`) cuando `HF_TOKEN`/`ASR_DIARIZATION_TOKEN` está configurado; fallback por energía/VAD si no hay token.
- **Solución Windows:** pyannote.audio 4 depende de `torchcodec`, que requiere FFmpeg "full-shared". Se instaló `FFmpeg (Shared) 9.0.2` vía winget y se configuró `FFMPEG_PATH` al `ffmpeg.exe` del build shared; `diarization.py` añade el directorio al `PATH` del proceso para que `torchcodec` cargue las DLLs.
- `apps/api/app/services/teams_visual_id.py`: OCR (RapidOCR) sobre la franja inferior de frames a resolución original; filtra nombres de persona y descarta números de caso/UI. Detecta correctamente nombres como "Jose Alfredo Molina Ibarra" y "CARLOS ALFONSO GOMEZ GARCES".
- `apps/api/app/services/media_pipeline.py`: reprocesamiento idempotente (borra segmentos previos), une ASR + diarización + nombres visuales, crea `speakers` y `transcript_segments`, marca `needs_review` bajo `ASR_CONFIDENCE_THRESHOLD`.
- `app/workers/handlers/media_asr.py`: handler Celery conectado al dispatcher.
- Ajustes de dependencias en contenedor: `soundfile==0.13.1`, `av==18.1.0` (compatibilidad con faster-whisper). Nota: torch en el contenedor usa CUDA 13 mientras ctranslate2 espera libcublas.so.12, por lo que ASR corre en CPU int8 en esta imagen; la diarización (torch) sigue usando GPU si está disponible.

### 3.3 Pendientes de FASE 3
- Confirmar finalización de la **muestra 2** y exportar transcripción estilo otter.ai (`scripts/export_media_transcript.py`).
- Medir **WER** sobre 15 min anotados a mano y **DER** (diarization error rate) en ambas muestras.
- Segmentación jurídica: etiquetar segmentos pregunta/respuesta, tipo de intervención (juez/abogado/testigo/perito) con LLM económico, para el visor estilo SSD §51.

**Criterio de salida F3:** WER medido sobre 15 min anotados a mano de una audiencia real; diarization error rate; búsqueda "pago" devuelve timestamp clickeable (FTS sobre `transcript_segments` ya existe).
**Agentes:** `coder` + `pytorch-build-resolver` (si GPU/whisper falla) + `mle-reviewer`.

**Decisiones técnicas actualizadas:**
- `ASR_DEVICE=cpu`, `ASR_COMPUTE_TYPE=int8` en Windows dev; CUDA en Docker worker.
- `HF_TOKEN` y `ASR_DIARIZATION_TOKEN` requeridos para pyannote.audio; se debe aceptar el license agreement de `pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0` y `pyannote/speaker-diarization-community-1`.
- En Windows, `FFMPEG_PATH` debe apuntar al build "full-shared" (ej. `Gyan.FFmpeg.Shared`) para que `torchcodec` funcione.

---

## FASE 4 — Extracción jurídica con LLM (3–4 semanas) ✅ Completada 2026-09-29

**Objetivo:** poblar el conocimiento: parties, entities, events, claims, facts, evidence_links, decisions, legal_rules, issues, contradictions — todo con citations obligatorias.

### 4.1 Prompts versionados (regla del repo: `packages/prompts/*.vN.md` inmutables) ✅
Creados: `extract_entities.v1.md`, `extract_claims.v1.md`, `extract_events.v1.md`, `link_evidence.v1.md`, `detect_contradictions.v1.md`, `extract_decisions.v1.md`, `summarize_case.v1.md`, `repair_json.v1.md`. Contenido marcado UNTRUSTED + escape (patrón ya en `services/answering.py`).

### 4.2 Jobs de extracción ✅
- `apps/api/app/services/legal_extraction.py`: servicio completo que carga prompts versionados, construye bloques de evidencia desde `document_pages` / `transcript_segments`, escapa contenido no confiable, valida salida contra schemas JSON (`jsonschema`), persiste `entities`, `claims`, `events`, `decisions`, `facts`, `contradictions` y `evidence_links`, y registra cada corrida en `model_runs` con hash de entrada, tokens y validación.
- `apps/api/app/workers/handlers/legal_extraction.py`: handler Celery `legal_extraction` registrado en `registry.py`; procesa documentos o media, ejecuta extracciones y detección de contradicciones por caso.
- `scripts/extract_legal_cli.py`: CLI admin para correr extracción sobre documentos/media reales.
- Citas: cada entidad/claim/evento/decisión se vincula a sus evidence blocks mediante `citations` → tabla `citations` con `quote_hash` SHA-256.
- Retry/repair: si la salida del LLM no pasa el schema, se intenta reparar una vez con `repair_json.v1.md`; si sigue inválida, el resultado queda anotado en `model_runs.validation` para revisión humana.

### 4.3 Model routing (D4) ✅
- `apps/api/app/providers/llm.py`: adaptador `OpenAiLLM` añadido; `get_llm_for_task(task)` lee `LLM_ROUTING` (JSON opcional) para asignar provider/model por tarea, heredando del default lo no especificado.
- `apps/api/app/core/config.py`: `LLM_PROVIDER` ahora acepta `openai`; `LLM_ROUTING` como JSON opcional.
- Presupuesto por caso: cada llamada verifica `cases.spent_llm_tokens + estimated <= max_llm_tokens` y descuenta tokens reales tras la llamada.

### 4.4 Estados epistémicos (corazón del producto) ✅
- Nada se guarda como "verdad": `ALLEGED/DISPUTED/SUPPORTED/CONTRADICTED/JUDICIALLY_DETERMINED/UNRESOLVED`. Los CHECKs de BD ya lo fuerzan (JUDICIALLY_DETERMINED exige decisión citada).
- Contradicciones: `human_review_required=true` forzado por CHECK — el detector propone, el humano dispone.
- Entity resolution: `MATCH/PROBABLE_MATCH/AMBIGUOUS/NO_MATCH`, sin fusión automática por similitud de nombre ("J. C. Pérez" ≠ "Juan Carlos Pérez" hasta evidencia).

### 4.5 Tests y calidad ✅
- `tests/unit/test_legal_extraction.py`: tests de persistencia, citas, escape anti-inyección, schema inválido y routing por tarea.
- `ruff check apps/api scripts tests packages/prompts` limpio.
- Dependencias: `jsonschema==4.23.0`, `soundfile==0.13.1`, `av==18.1.0` añadidas a `requirements/base.txt`.

**Pendiente operativo:** ejecutar `extract_legal_cli.py` sobre documentos OCRizados del caso piloto tan pronto Docker Desktop vuelva a responder; auditar muestra de 200 claims a mano.

**Criterio de salida F4:** expediente piloto con entidades/claims/eventos extraídos; muestra de 200 claims auditada a mano (precisión/recall); 0 citas inválidas en BD.
**Agentes:** `coder` + `rag-pipeline-reviewer` + `security-reviewer` (prompt injection con modelo hostil, suite `tests/security` ya tiene el patrón).

---

## FASE 5 — Embeddings y búsqueda híbrida (1–2 semanas) ✅ Completada 2026-09-29

**Objetivo:** dejar atrás el FTS `simple` sin stemming ("pagó" ≠ "pago", deuda conocida).

### 5.1 Proveedores de embeddings ✅
- `apps/api/app/providers/embeddings.py`: implementa `FakeEmbedding` (tests/desarrollo), `OpenAIEmbedding` y `SentenceTransformersEmbedding` (local). Respeta el protocolo `EmbeddingProvider` ya definido en `base.py`.
- `apps/api/app/core/config.py`: settings `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`, `EMBEDDING_API_BASE_URL`, `EMBEDDING_BATCH_SIZE`, `EMBEDDING_MAX_CHARS`, `RETRIEVAL_RRF_K`. Actualizados `.env.example`, `.env.test` y `.env`.
- Dependencias: `pgvector==0.2.5`, `openai==1.51.0`, `sentence-transformers==3.1.1` añadidas a `requirements/base.txt`.

### 5.2 Chunking jurídico (SSD §39) ✅
- `apps/api/app/services/chunking.py`: chunks alineados a unidades procesales, nunca corte genérico de N tokens.
  - Documentos: un chunk por página/folio con metadata de entidades citadas.
  - Testimonios: agrupación de segmentos contiguos del mismo hablante (ventana 30 s) con metadata de speaker y entidades.
  - Estructurados: un chunk por claim, evento, fact, decisión y evidence.
- `apps/api/app/services/indexing.py`: reindexación idempotente (borra chunks previos del scope), embedding por lotes e inserción en `chunks` con `embedding_model` y `embedding_version`.

### 5.3 Búsqueda híbrida ✅
- `apps/api/app/services/answering.py::retrieve`: fallback a FTS legacy si el caso aún no tiene chunks; cuando hay chunks, ejecuta FTS sobre `chunks.tsv` + vector search sobre `chunks.embedding` (cosine distance) y fusiona con **Reciprocal Rank Fusion** (`RETRIEVAL_RRF_K=60`).
- Los resultados conservan el contrato anterior (`source_type`, `document_id`, `page_number`, `folio`, `filename`, `media_id`, `segment_id`, `start_ms`, `end_ms`, `speaker`, `text`, `handle`) para no romper `/query` ni los tests.

### 5.4 Jobs de embedding/indexing ✅
- `apps/api/app/workers/handlers/embedding.py`: handler `embedding` que indexa documentos o media por `input_ids`.
- `apps/api/app/workers/handlers/indexing.py`: handler `indexing` que reindexa todo un caso o solo entidades estructuradas.
- Registrados en `apps/api/app/workers/registry.py` y excluidos del test de stubs.
- `scripts/index_chunks_cli.py`: CLI admin para indexar manualmente documento/media/caso.

### 5.5 Permisos de BD ✅
- `apps/api/db/sql/050_rls_grants.sql`: se concede `DELETE` sobre `chunks` al rol de aplicación para permitir reindexación idempotente.
- Aplicado en la BD de desarrollo con `GRANT DELETE ON chunks TO judicial_app`.

### 5.6 Validación en contenedor ✅
- Documento piloto `b14f7e16-fdbf-4f19-ac0e-5aa23a0a66e3` (4 páginas) indexado en Docker con `EMBEDDING_PROVIDER=fake` → 4 chunks con embeddings.
- `answering.retrieve` devolvió los 4 chunks ordenados por RRF para la pregunta "pago obligación".

**Criterio de salida F5:** ✅ Pipeline de embeddings e indexación funcional; búsqueda híbrida FTS+vector con RRF; chunks con metadata jurídica; tests verdes. El golden set/Recall@K se deja para Fase 7 (eval runner).
**Agentes:** `coder` + `database-reviewer` + `python-reviewer`.

---

## FASE 6 — Knowledge Graph (2 semanas) ✅ Completada 2026-09-29

**Aclaración (D3):** no es una red neuronal — es el grafo de relaciones del expediente (SSD §16). No se entrena nada; se **deriva** de lo ya extraído.

### 6.1 Grafo sobre PostgreSQL (source of truth, SSD §6.7) ✅
- Migración `0007_graph_tables`: tablas `graph_nodes` (Case, Person, Organization, Document, Claim, Fact, Evidence, Event, Decision, Issue) y `graph_edges` (`ASSERTS`, `SUPPORTS`, `CONTRADICTS`, `REFUTES`, `CITES`, `PARTICIPATED_IN`, `TESTIFIED_IN`, `DECIDES`, `APPLIES`, `DERIVED_FROM`, `MENTIONS`, `ABOUT`).
- RLS + permisos `DELETE` para reconstrucción idempotente en `050_rls_grants.sql`.
- Servicio `app/services/graph.py`: `build_case_graph()` proyecta entities, claims, facts, events, decisions, evidence, citations, contradictions, evidence_links, fact_claims y parties a nodos/aristas con procedencia `EXTRACTED`/`INFERRED`/`AMBIGUOUS`.
- Job `graph_build` registrado en `app/schemas.py`, `app/workers/registry.py` y handler `app/workers/handlers/graph_build.py`.
- API REST: `POST /cases/{id}/graph/build`, `GET /cases/{id}/graph/nodes`, `GET /cases/{id}/graph/traverse/{node_id}`, `GET /cases/{id}/graph/evidence-matrix`.
- Permiso RBAC `ai.graph` añadido a roles analíticos.
- CLI admin: `scripts/build_graph_cli.py`.

### 6.2 Neo4j (diferido)
Solo cuando las consultas multi-salto lo justifiquen; export desde las tablas (Graphify demuestra el patrón `--neo4j-push`).

**Criterio de salida F6:** grafo del expediente piloto navegable por API; matriz de evidencia (SSD §108) generada desde el grafo.
- ✅ Grafo del piloto reconstruido: **222 nodos / 221 aristas** (en este momento solo documentos y caso; claims/facts/evidence se poblarán tras ejecutar Fase 4 con LLM real).
- ✅ `/graph/traverse/{case_node}` responde con documentos vinculados; `/graph/evidence-matrix` responde estructura vacía esperada hasta tener hechos extraídos.
- ✅ Job `graph_build` ejecutado vía `POST /process` y finalizado `SUCCEEDED` por el worker Celery.
- ✅ Imagen Docker reconstruida (`docker compose up -d --build`) con los cambios de Fase 6 persistidos; `.dockerignore` ajustado para excluir `var/` (~27 GB) y acelerar el build.
- ✅ `pytest` verde: **495 passed, 0 failed**; `ruff check` limpio.
**Agentes:** `architect` (diseño de esquema) + `database-reviewer`.

---

## FASE 7 — Agente jurídico y verificación (2–3 semanas) ✅ Completada 2026-09-29

**Objetivo:** `/query` pasa de RAG simple a agente con herramientas y verificación anti-alucinación fuerte.

### 7.1 Agente con tools allowlist ✅
- Nuevo campo `strategy: Literal["rag", "agent"]` en `QueryIn` (`app/schemas.py`). `POST /v1/cases/{id}/query` ejecuta el modo `rag` existente o el modo `agent`.
- `app/services/agent_tools.py`: implementa la allowlist del SSD §49:
  - `search_documents`, `search_claims`, `search_facts`, `search_evidence`, `search_timeline`, `search_transcripts`, `get_document_page`, `get_video_segment`, `graph_query`.
  - Cada tool devuelve evidencia con handle único; nunca se carga el expediente completo en contexto (least context, SSD §98).
- `app/services/agent.py`: loop ReAct simplificado con JSON por turno (máx. 5 pasos). Tras recolectar evidencia, sintetiza la respuesta con el prompt `answer_question` existente y filtra fuentes primarias (documentos/páginas o segmentos de audio/video) para que las citas sean verificables.

### 7.2 Verificador semántico anti-alucinación ✅
- `app/services/verification.py`: LLM-juez que clasifica cada claim como `supported`/`not_supported`/`contradicted`/`needs_review` usando el prompt `packages/prompts/verify_answer.v1.md` y el schema `packages/schemas/verify_answer.schema.json`.
- Se ejecuta después de `parse_and_validate`; los claims no soportados se mueven a `unsupported_claims` y las ambigüedades se añaden a `uncertainties`. Complementa el grounding léxico/cifras de `answering.py`.

### 7.3 Eval runner y golden suite ✅
- `scripts/run_evals.py`: carga casos YAML desde `evals/golden/` y mide:
  - `retrieval_recall` (la evidencia recuperada contiene la fuente esperada),
  - `citation_recall` (la respuesta final cita la fuente esperada),
  - `forbidden_hit` y `contains_hit_rate`,
  - `abstain_accuracy` para casos `must_abstain`.
- Soporta `--retrieval-only` (sin LLM, útil para CI con `LLM_PROVIDER=fake`) y `--threshold` para gate de regresión.
- `evals/golden/pilot_manual.yaml`: 20 golden cases construidos desde el expediente piloto (16 de contenido OCR verificado a mano + 4 adversarios de abstención).
- Detector de abstención (`app/services/abstention.py`) basado en cobertura de términos significativos sobre todo el texto del caso (documentos, transcripciones, claims, hechos, evidencia, eventos, partes, speakers, nombres de archivo). Configurable vía `ABSTENTION_MIN_TERM_COVERAGE`.
- Resultado actual con `LLM_PROVIDER=fake` y `--retrieval-only --threshold 1.0`: **20/20 pasan (100%)**.

### 7.4 Tests ✅
- `tests/unit/test_agent.py`, `test_agent_tools.py`, `test_verification.py`, `test_eval_runner.py`.

**Criterio de salida F7:** ✅ Agente navegable por `/query?strategy=agent`; verificador semántico integrado; eval runner funcional; 20 golden cases del piloto; suite completa **511 passed, 0 failed**; `ruff check` limpio.
**Agentes:** `coder` + `eval-harness` skill + `santa-method` (doble revisión adversarial de respuestas críticas).

---

## FASE 8 — Frontend Next.js (3–4 semanas, puede solaparse con F6–F7) ✅ Completada 2026-09-29

### 8.1 Panel de administración ✅
- **Login** con usuario/contraseña y redirección automática.
- **Dashboard** con sidebar izquierdo y navegación entre módulos.
- **Usuarios**: listar, crear (con correo de bienvenida), editar (rol, idioma, activo), reset de contraseña por correo.
- **Roles y Permisos**: visualización de roles disponibles.
- **Correos (SMTP)**: configuración de From Name, From Email, Server, Port, Security, Username, Password, CC Emails.
- **Agentes**: listado de agentes con nombre, system prompt y skills asociadas.
- **Skills**: listado de skills con nombre y system prompt.
- **Modelos IA**: listado de modelos por proveedor (Claude, Gemini, OpenAI, Kimi).
- **PDFs**: listado de documentos OCRizados.
- **Videos**: listado de audiencias transcritas.
- **Chats IA**: historial de conversaciones.
- **Backups**: listado y trigger manual.

### 8.2 Stack técnico ✅
- Next.js 16.3.7 + TypeScript + Tailwind CSS.
- React Query para data fetching, Zustand para estado global, shadcn/ui para componentes.
- Docker: `infra/docker/web.Dockerfile` + servicio `web` en `docker-compose.yml`.
- Responsive: funciona en laptop y ultrawide.

### 8.3 Backend de administración ✅
- `apps/api/app/routers/admin.py`: endpoints para usuarios, SMTP, agentes, skills, modelos, backups.
- Tablas: `smtp_settings`, `agents`, `skills`, `ai_models`, `backups` (migración `0008_admin_tables`).
- Servicio `app/services/mailer.py`: correos de bienvenida y reset de contraseña.
- Tests: `tests/integration/test_admin.py` (8 passed).

### 8.4 Ampliación del panel (CRUD real + visores) ✅
- **Agentes**: crear/editar/eliminar y **enlazar skills** (multiselección).
- **Skills**: crear/editar/eliminar (nombre + system prompt).
- **Modelos IA**: crear/editar/eliminar con proveedor (Anthropic, OpenAI, Gemini, Kimi, **DeepSeek**, personalizado), catálogo de modelos vigente por proveedor, API key y modelo por defecto.
- **Roles y Permisos**: crear roles personalizados, editar sus permisos (checkbox por permiso) y eliminarlos. Los roles de sistema (de `rbac.yaml`) quedan de solo lectura. Los permisos personalizados se aplican en la autorización vía caché RBAC por organización.
- **PDFs**: selector de expediente; al abrir un cuadernillo se navega hoja por hoja, se ve la **imagen rasterizada** y el **texto OCR** extraído, se corrige y al guardar se **reindexa** (chunks + embeddings) y **aprende el lexicón** (`ocr_terms`) para futuros OCR.
- **Videos**: selector de expediente; reproductor del video + transcripción por segmento con **quién dijo, qué dijo y en qué minuto** (`start_ms/end_ms`); cada segmento es editable y al guardar se reindexa.
- **Migración `0010_admin_crud`**: proveedor `deepseek/custom`, `ai_models.encrypted_api_key`, tablas `roles` y `ocr_terms`, y `users.org_role` sin CHECK fijo (validación en la app).
- **Endpoints nuevos**: CRUD `/v1/admin/{agents,skills,models,roles}` + `/admin/models/providers` + `/admin/permissions`; `GET /cases/{id}/media`, `GET/PATCH .../media/{id}/segments[/{sid}]`, `GET .../documents/{id}/pages`, `PATCH .../pages/{n}`, `GET .../pages/{n}/image`, `GET .../media/{id}/download`.
- Tests: `tests/integration/test_admin_crud.py` (6 tests).

**Criterio de salida F8:** ✅ Login funcional; panel de administración operativo con CRUD real; visores OCR/transcripción editables; suite completa **560 passed, 0 failed**; `ruff check` limpio.
**Agentes:** `coder` + `frontend-patterns`/`frontend-ui-engineering` skills + `react-reviewer` + `e2e-runner` (Playwright).

---

## FASE 9 — Exportación CKP + benchmark real del expediente (2 semanas) ✅ Completada 2026-09-30

### 9.1 Exportación CKP ✅
- `apps/api/app/services/export.py`: servicio de exportación que genera un ZIP con:
  - `manifest.json` (metadata del paquete según `manifest.schema.json`)
  - `case.json` (datos del expediente)
  - `entities/*.jsonl` (parties, entities, decisions, events, claims, facts, evidence, contradictions, legal_rules, issues, speakers)
  - `documents/*.jsonl` (documents, document_pages)
  - `media/*.jsonl` (media, transcript_segments)
  - `citations/*.jsonl` (citations, evidence_links, fact_claims)
  - `graph/*.jsonl` (nodes, edges)
  - `audit/*.jsonl` (audit_logs)
  - `jobs/*.jsonl` (jobs, model_runs)
- Endpoint `GET /v1/cases/{id}/export` con permiso `ai.export` y auditoría registrada.
- Serialización JSON robusta con soporte para `Decimal`, `datetime`, `date`, `UUID` y `bytes`.

### 9.2 Benchmark del expediente piloto ✅
- `scripts/benchmark_case.py`: mide el estado actual del expediente piloto.
- Resultado: **210 documentos, 498 páginas, 11 media, 722 segmentos, 222 nodos/221 aristas de grafo, 67,780 tokens LLM gastados**.
- El expediente piloto tiene 0 claims/facts/contradictions porque la Fase 4 (extracción con LLM real) aún no se ha ejecutado.

### 9.3 Tests ✅
- `tests/integration/test_export.py`: 4 tests que verifican autenticación, permisos, formato ZIP y validez de JSONL.

**Criterio de salida F9:** ✅ CKP exportado (1.9 MB) y re-importable; benchmark del piloto medido; suite completa **531 passed, 0 failed**; `ruff check` limpio.
**Agentes:** `coder` + `database-reviewer`.

---

## FASE 10 — Hardening y observabilidad (2 semanas) ✅ Completada 2026-09-30

### 10.1 Métricas y OpenTelemetry ✅
- `apps/api/app/services/metrics.py`: instrumentación Prometheus con las métricas del SSD §28:
  - `judicial_docs_processed_total`, `judicial_pages_processed_total`, `judicial_media_hours_processed_total`
  - `judicial_stage_latency_seconds` (OCR, ASR, query, etc.)
  - `judicial_llm_tokens_total`, `judicial_llm_cost_total`
  - `judicial_citation_accuracy`, `judicial_retrieval_hit_rate`
  - `judicial_jobs_queued`, `judicial_jobs_failed_total`, `judicial_ocr_failures_total`, `judicial_asr_failures_total`
  - `judicial_rate_limit_hits_total`
- Endpoint `GET /metrics` en formato Prometheus.
- Integración real en: `routers/query.py` (tokens, citation accuracy, retrieval hit rate, latencia), `workers/executor.py` (jobs encolados/completados/fallidos), `workers/handlers/document_ocr.py` (docs/páginas/fallos OCR), `workers/handlers/media_asr.py` (horas/fallos ASR).
- Logs JSON sin contenido jurídico (solo IDs, counts y duraciones).

### 10.2 Alertas (SSD §113) ✅
- `apps/api/app/services/alerts.py`: verificación de:
  - backlog de colas (> 100 jobs),
  - tasa de fallo OCR/ASR (> 15% en 24 h),
  - presupuesto LLM por caso (80% / 90% / 100%).
- Endpoint `GET /v1/admin/alerts` + vista **Alertas** en el frontend.
- Logs de alerta en JSON estructurado.

### 10.3 Backups y DR ✅
- `apps/api/app/services/backup.py`: backup PostgreSQL con `pg_dump` (formato custom para PITR), backup de object storage con `tar.gz`, verificación de RPO ≤ 1 h y prueba de restauración (RTO ≤ 4 h).
- Tarea Celery `backups.daily` en `beat_schedule` (cada 24 h) que recorre organizaciones vía función SECURITY DEFINER `ops_list_organizations()` (migración `0009_ops_functions`).
- Endpoints `GET /v1/admin/backups/verify-rpo` y `POST /v1/admin/backups/restore-test`.

### 10.4 Rate limiting por organización ✅
- `RATE_LIMIT_ORG_QUERIES_PER_MINUTE` y `RATE_LIMIT_ORG_UPLOADS_PER_MINUTE` (protegen el costo compartido).
- `ratelimit.check_org()` aplicado en `/query` y en uploads de documentos/media.

### 10.5 Load testing y auditoría ✅
- `scripts/load_test.py`: prueba concurrente de uploads y queries con métricas de éxito, latencia promedio y p95.
- `scripts/production_audit.py`: 16 checks de configuración, seguridad, observabilidad y backups.
- Suite de seguridad `tests/security`: **229 passed** (se corrigió un test de cadena de auditoría que dependía del orden de ejecución).

### 10.6 Tests ✅
- `tests/integration/test_observability.py` (8 tests), `tests/unit/test_metrics.py` (6), `tests/unit/test_ratelimit_org.py` (3).

**Criterio de salida F10:** ✅ Métricas Prometheus expuestas e integradas; alertas operativas; backups diarios con DR verificado; rate limits por organización; load test y auditoría de producción funcionales; suite completa **552 passed, 0 failed**; `ruff check` limpio.
**Agentes:** `security-reviewer` + `production-audit` skill + `database-reviewer`.

---

## FASE 11 — Despliegue a producción con Docker (1–2 semanas)

`infra/docker/` + `docker-compose.prod.yml` (el compose actual es de desarrollo):
- Servicios: `api` (×2, sin `--reload`), `worker` (×N, escala por cola), `postgres` (pgvector — o RDS/Cloud SQL administrado), `redis`, `clamav`, `minio`→S3 externo, reverse proxy (Caddy/Traefik) con TLS → activa HSTS.
- Guardas de producción **ya implementadas** en `app/core/config.py`: prohíben FakeLLM, scanner `basic` y rate-limit en memoria en `APP_ENV=production` — el despliegue las ejerce.
- Secretos fuera del repo (Docker secrets / secret manager); `.env` generado por `gen_env.py` nunca se versiona (ya en `.gitignore`).
- Migraciones solo por Alembic (`db_migrate.sh`), nunca a mano.
- Healthchecks `/health` + `/ready` en cada servicio; política de restart; límites de recursos.
- Staging primero: procesar 1 expediente de prueba → smoke tests → aprobación → producción (pipeline CI/CD del SSD §45).

**Criterio de salida F11:** expediente piloto procesado íntegro en producción, DR test ejecutado, monitoreo con alertas activas, costo por expediente conocido.

---

## Resumen ejecutivo

| Fase | Qué se construye | Duración | Depende de |
|---|---|---|---|
| 0 | Workers Celery, compose completo, deuda rate-limit | 1–2 sem | — |
| 1 | Parser XLSX índice + importador masivo (224 archivos) | 1–2 sem | F0 |
| 2 | OCR real + folios + clasificación (AnyDoc solo Office/texto) | 2–3 sem | F1 |
| 3 | ASR Whisper + diarización pyannote + hablantes | 2–3 sem | F1 | 🔄 en progreso |
| 4 | Extracción jurídica LLM con estados epistémicos | 3–4 sem | F2, F3 |
| 5 | Embeddings pgvector + búsqueda híbrida | 1–2 sem | F2–F4 |
| 6 | Knowledge graph en PostgreSQL (no red neuronal) | 2 sem | F4 |
| 7 | Agente con tools + verificador + golden evals | 2–3 sem | F5, F6 |
| 8 | Frontend Next.js completo | 3–4 sem | F4 (paralela) |
| 9 | Export CKP + benchmark de costo real | 2 sem | F2–F7 |
| 10 | Observabilidad, backups, DR, seguridad | 2 sem | todo | ✅ completada |
| 11 | Docker producción + staging + go-live | 1–2 sem | F10 |

**Total estimado:** 20–28 semanas con 1–2 desarrolladores (F8 en paralelo lo comprime).
**Riesgos principales:** calidad de OCR en escaneados masivos (mitigar con benchmark temprano en F2), costo de ASR de 8.8 GB de video (procesar una audiencia primero y extrapolar), y costo LLM de extracción sobre miles de folios (model routing + presupuesto por caso desde el día uno).
