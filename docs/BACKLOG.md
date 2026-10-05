# Estado y backlog

Leyenda: ✅ implementado y probado · 🧩 especificado, con interfaz o esquema listo · ⏳ pendiente.

## Sprint 0: fundaciones (✅)
| Ítem | Estado |
|---|---|
| Esquema PostgreSQL (28 tablas) con CHECKs epistémicos, índices y FTS bilingüe `simple` | ✅ |
| RLS forzado multi-tenant + trigger `enforce_same_org` + roles owner/app separados | ✅ |
| Migración Alembic reversible (up/down/up verificado) | ✅ |
| Configuración 100 % por entorno, `gen_env.py` con secretos aleatorios, guardas de producción | ✅ |
| i18n es/en (errores, mensajes, enums) con negociación `Accept-Language` | ✅ |
| Auth: argon2id, JWT con alg fijado, refresh con rotación y revocación de familia, bloqueo, rate limit | ✅ |
| RBAC como código (org ∩ expediente) | ✅ |
| Auditoría append-only con cadena de hash | ✅ |
| Semillas sintéticas + 10 cuentas de prueba | ✅ |
| Setup automático Claude Code web, bootstrap local, Docker, CI, Makefile | ✅ |

## Sprint 1: ingesta y visor (parcial)
| Ítem | Estado |
|---|---|
| Carga de documentos/media: magic bytes, saneo de nombre, EICAR/ClamAV, límite de tamaño, dedup por sha256 | ✅ |
| Almacenamiento write-once direccionado por contenido (local/S3) + verificación de integridad en descarga | ✅ |
| Legal hold y solicitud de borrado auditada | ✅ |
| Visor de página y resolución de citas (documento+página / media+timestamp) con `quote_hash` | ✅ |
| Jobs idempotentes (`/process`) ejecutados por worker Celery + Redis (handlers stub de Fase 0), con sweeper de jobs huérfanos vía Celery Beat | ✅ |
| OCR (proveedor pluggable, confianza por página) | 🧩 `providers/base.py` → ⏳ Tesseract/Textract |
| Clasificación de documentos | 🧩 → ⏳ |
| Rasterizado de páginas para verificación visual | ⏳ |

## Sprint 2: audio/video
| ASR con timestamps + diarización (p. ej. Whisper + pyannote) | 🧩 interfaces, tablas `media`/`speakers`/`transcript_segments` → ⏳ |
| Reproductor con salto a timestamp | ⏳ (frontend) |

## Sprint 3: extracción y conocimiento
| Extracción LLM de entidades/claims/eventos con JSON Schema (`packages/schemas`) | 🧩 esquemas + prompts versionados → ⏳ job |
| Detección de contradicciones (siempre con revisión humana) | 🧩 tabla + reglas en BD → ⏳ detector |
| Embeddings (`chunks.embedding vector(EMBEDDING_DIMENSIONS)`) + búsqueda híbrida | 🧩 → ⏳ (hoy: FTS) |

## Sprint 4: consulta y revisión (✅ núcleo)
| `/query` con RAG, escape de contenido, contrato validado, grounding de términos y cifras, presupuesto por caso | ✅ |
| Revisión humana con bloqueo optimista e historial original | ✅ |
| Timeline, hechos, evidencia, contradicciones relacionadas en respuestas | ✅ |

## Sprint 5: producto
| Frontend Next.js (visor PDF, reproductor, timeline, revisión) | ⏳ |
| Exportación del Case Knowledge Package (manifest + JSON Schemas) | 🧩 esquema `manifest` → ⏳ endpoint |
| Evaluación automática con conjunto dorado (`evals/golden`) | 🧩 formato → ⏳ runner con métricas de §133 |
| Gestión de usuarios / invitaciones / SSO | ⏳ |
| Observabilidad (OpenTelemetry, métricas de costo por caso) | ⏳ |

## Deuda conocida
- Los handlers de los 7 tipos de job son stubs (Fase 0): marcan SUCCEEDED y registran el no-op en `model_runs`. Se implementan en las fases 2–5 del plan.
- La constancia de los jobs vive en `model_runs` con `task='job:<tipo>'` (marcador distingible). **Antes de las fases 2–5**: migrar resultados de jobs a tabla/columna propia (p. ej. `jobs.result jsonb`) y dejar `model_runs` sólo para ejecuciones reales de modelos.
- `.env` locales generados antes de Fase 0 necesitan las variables `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CELERY_TASK_ALWAYS_EAGER`, `JOB_STALE_MINUTES` y `JOB_TIME_LIMIT_SECONDS` (regenerar con `scripts/gen_env.py --force` o añadirlas a mano).
- El `actor_id` del payload de `jobs.run` es falsificable por quien pueda escribir en el broker. La fuente confiable ya existe (`jobs.created_by`); migración a futuro: eliminar `actor_id` del payload y leerlo de la fila del job en el executor.
- Defensa en profundidad pendiente: `jobs_reap_stale(p_stale_minutes)` acepta umbrales absurdos si se llama directo por SQL; envolver con `GREATEST(p_stale_minutes, 5)` en una futura migración.
- Limitación dev/test: con `CELERY_TASK_ALWAYS_EAGER=true` los reintentos de `run_job` se ejecutan inline dentro de la petición; el backoff (`retry_countdown`) no se materializa como espera real. Sólo afecta a tests/desarrollo sin broker.
- La recuperación FTS con la configuración `simple` no hace stemming ("pagó" ≠ "pago"). Los embeddings lo resolverán en el Sprint 3.
- El grounding léxico es una red de seguridad, no una verificación semántica. El Sprint 3 debe añadir un verificador NLI o un LLM juez.
- **OCR de manuscrito:** RapidOCR/PP-OCR y Tesseract leen bien el texto impreso pero no la letra manuscrita (los expedientes escaneados mezclan ambos). Mitigaciones implementadas: post-proceso que limpia artefactos de formulario (`services/ocr_postprocess.py`), proveedor `docling_latin` (modelo de reconocimiento latino) y el gancho `OCR_PROVIDER=handwriting` con `OCR_HANDWRITING_MODEL` (segunda pasada sobre líneas de baja confianza; `services/handwriting_ocr.py`, deshabilitado por defecto). **Medición 2026-09-30** sobre las 17 páginas de baja confianza del cuaderno 0001: `microsoft/trocr-small-handwritten` bajó la proporción de palabras españolas (0.174→0.140), introdujo inglés y perdió dígitos; `microsoft/trocr-base-handwritten` no mejoró ninguna línea; `ifesther/trocr-spanish-handwritten` fue mixto (mejora en pág. 55, empeora en pág. 9) y muy lento (~54 s/página). Ningún modelo público es fiable para manuscrito judicial colombiano. **Solución encaminada:** pipeline de fine-tuning con las correcciones humanas (`scripts/build_handwriting_dataset.py` + `scripts/finetune_handwriting.py`, métricas CER/WER en `services/ocr_metrics.py`, detalle en `docs/HANDWRITING_OCR.md`). Alternativa descartada por ahora: proveedor document-AI en la nube (opt-in; enviaría páginas fuera de la máquina).
