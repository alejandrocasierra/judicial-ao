# SSD — Plataforma de Estructuración Inteligente de Expedientes Judiciales

**Versión:** 1.0  
**Fecha:** 2026-09-25  
**Estado:** Especificación técnica de sistema (SSD)  
**Idioma:** Español  
**Tipo de sistema:** Plataforma SaaS/API de procesamiento documental y multimodal para expedientes judiciales  
**Objetivo:** Convertir expedientes judiciales heterogéneos —documentos, escaneos, imágenes, audios y videos de audiencias/interrogatorios— en una representación estructurada, trazable, consultable y apta para sistemas de IA jurídica.

---

# 1. Resumen ejecutivo

La plataforma recibe expedientes judiciales completos y los transforma en un **Case Knowledge Package (CKP)** compuesto por:

1. documentos originales preservados;
2. OCR y texto normalizado;
3. clasificación documental;
4. extracción de metadatos;
5. segmentación semántica;
6. transcripción de audio/video con identificación de hablantes;
7. línea de tiempo procesal;
8. personas, organizaciones y entidades;
9. hechos y afirmaciones;
10. hechos controvertidos;
11. pruebas y relaciones evidencia-hecho;
12. declaraciones testimoniales;
13. contradicciones;
14. normas y jurisprudencia mencionadas;
15. decisiones judiciales;
16. grafo de conocimiento;
17. índices de búsqueda léxica y vectorial;
18. provenance/evidence links para cada afirmación;
19. artefactos JSON/JSONL diseñados para consumo por agentes de IA.

La propiedad central del sistema será:

> **Toda afirmación generada por IA debe poder rastrearse hasta su fuente primaria: documento + página/folio, o video/audio + timestamp + hablante.**

El sistema no debe tratar una afirmación de una parte o testigo como verdad automáticamente. Debe preservar la distinción entre **afirmación, evidencia, inferencia y determinación judicial**.

---

# 2. Objetivos

## 2.1 Objetivo principal

Crear una infraestructura que convierta un expediente judicial multimodal en una representación estructurada que pueda ser consumida eficientemente por modelos de lenguaje y agentes jurídicos.

## 2.2 Objetivos secundarios

- Reducir el costo cognitivo de revisar expedientes extensos.
- Permitir consultas semánticas y exactas.
- Mantener trazabilidad hasta la fuente original.
- Procesar documentos escaneados.
- Procesar videos largos de audiencias e interrogatorios.
- Identificar participantes y hablantes.
- Construir una cronología procesal.
- Detectar contradicciones y referencias cruzadas.
- Separar hechos alegados de hechos determinados judicialmente.
- Permitir re-procesamiento incremental.
- Mantener versiones de los resultados de IA.
- Facilitar auditoría humana.
- Preparar los datos para RAG y agentes jurídicos.
- Permitir procesamiento por lotes de expedientes grandes.

## 2.3 No objetivos del MVP

- Emitir decisiones judiciales.
- Sustituir al abogado.
- Determinar automáticamente la verdad de hechos controvertidos.
- Crear asesoramiento jurídico autónomo sin revisión.
- Alterar documentos originales.
- Eliminar evidencia contradictoria.
- Presentar inferencias de IA como hechos establecidos.

---

# 3. Principios de arquitectura

## 3.1 Source of Truth

Los archivos originales son inmutables y constituyen la fuente primaria.

Nunca se modifica el original. Toda transformación genera un artefacto nuevo.

## 3.2 Provenance-first

Cada elemento derivado debe almacenar provenance.

Ejemplo:

```json
{
  "claim_id": "CLM-000142",
  "text": "La parte demandada afirma haber realizado el pago.",
  "claim_type": "party_assertion",
  "source": {
    "document_id": "DOC-0042",
    "page": 87,
    "folio": "87",
    "char_start": 1832,
    "char_end": 1928
  },
  "confidence": 0.94
}
```

Para video:

```json
{
  "source": {
    "media_id": "MED-0008",
    "start_ms": 4421000,
    "end_ms": 4468000,
    "speaker_id": "SPK-03"
  }
}
```

## 3.3 Human-in-the-loop

Las operaciones críticas deben permitir revisión humana.

## 3.4 Idempotencia

Procesar dos veces el mismo archivo con la misma versión del pipeline no debe generar duplicados inconsistentes.

## 3.5 Versionado

Modelos, prompts, parsers, schemas y pipelines deben versionarse.

## 3.6 Reproducibilidad

Toda salida de IA debe registrar:

- modelo;
- versión;
- configuración;
- prompt/template;
- timestamp;
- versión del pipeline;
- entrada utilizada;
- resultado;
- validaciones.

## 3.7 Zero-trust interno

Cada servicio autentica y autoriza sus operaciones.

## 3.8 Privacy by design

El sistema debe asumir que contiene información jurídica altamente sensible.

---

# 4. Arquitectura de alto nivel

```text
                         ┌──────────────────────┐
                         │      WEB / API       │
                         └──────────┬───────────┘
                                    │
                              API Gateway
                                    │
                 ┌──────────────────┼──────────────────┐
                 │                  │                  │
                 ▼                  ▼                  ▼
             Auth/IAM          Case Service       Upload Service
                                    │                  │
                                    │                  ▼
                                    │             Object Storage
                                    │             Originals/Raw
                                    │
                                    ▼
                              Job Orchestrator
                                    │
                 ┌──────────────────┼──────────────────┐
                 │                  │                  │
                 ▼                  ▼                  ▼
          Document Pipeline    Media Pipeline     Metadata Pipeline
                 │                  │                  │
                 ▼                  ▼                  ▼
          OCR / Parsing       ASR / Diarization   Entity Extraction
                 │                  │                  │
                 └──────────────────┼──────────────────┘
                                    ▼
                           Normalization Layer
                                    │
                                    ▼
                         Structured Case Model
                                    │
                 ┌──────────────────┼──────────────────┐
                 ▼                  ▼                  ▼
            PostgreSQL          Search Index       Vector DB
                 │                  │                  │
                 └──────────────────┼──────────────────┘
                                    ▼
                             Knowledge Graph
                                    │
                                    ▼
                         Evidence-aware RAG
                                    │
                                    ▼
                           Legal AI / Agents
                                    │
                    ┌───────────────┼────────────────┐
                    ▼               ▼                ▼
                 Summary          Q&A           Investigation
                    │               │                │
                    └───────────────┼────────────────┘
                                    ▼
                             Citation Layer
                                    │
                                    ▼
                         Human Review / Audit
```

---

# 5. Arquitectura lógica

## 5.1 Capas

### Capa 1 — Ingesta

Responsabilidades:

- uploads;
- importación por API;
- importación masiva;
- checksum;
- deduplicación;
- antivirus;
- clasificación inicial;
- metadata básica.

### Capa 2 — Raw storage

Almacena:

- PDF;
- imágenes;
- videos;
- audios;
- ZIP;
- DOCX;
- XLSX;
- PPTX;
- HTML;
- TXT;
- otros formatos soportados.

### Capa 3 — Document intelligence

Incluye:

- OCR;
- layout analysis;
- detección de tablas;
- extracción de encabezados;
- clasificación;
- extracción de metadatos;
- identificación de folios.

### Capa 4 — Media intelligence

Incluye:

- extracción de audio;
- normalización;
- speech-to-text;
- diarización;
- segmentación;
- timestamps;
- identificación de hablantes;
- detección de preguntas/respuestas;
- identificación de participantes.

### Capa 5 — Legal normalization

Convierte contenido bruto en:

- hechos;
- afirmaciones;
- posiciones;
- pruebas;
- entidades;
- eventos;
- normas;
- decisiones;
- contradicciones.

### Capa 6 — Knowledge layer

Incluye:

- PostgreSQL;
- full-text search;
- vector search;
- knowledge graph.

### Capa 7 — AI layer

Incluye:

- retrieval;
- reranking;
- contextualization;
- synthesis;
- citation;
- agent orchestration.

### Capa 8 — Presentation

Incluye:

- dashboard;
- expediente;
- timeline;
- documentos;
- video;
- transcript;
- hechos;
- pruebas;
- contradicciones;
- chat.

---

# 6. Arquitectura física recomendada para MVP

## 6.1 Backend

Recomendación:

- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- Celery o Temporal para workflows
- Redis
- PostgreSQL

## 6.2 Frontend

- Next.js
- TypeScript
- React
- Tailwind CSS
- componente de PDF viewer
- video player con navegación por timestamps
- visualización de grafos

## 6.3 Storage

Object Storage compatible con S3.

Estructura:

```text
cases/
  {case_id}/
    originals/
    normalized/
    OCR/
    media/
    transcripts/
    embeddings/
    exports/
    audit/
```

## 6.4 Base relacional

PostgreSQL.

## 6.5 Search

OpenSearch/Elasticsearch o PostgreSQL FTS para MVP.

## 6.6 Vector database

Opciones:

- pgvector para MVP;
- Qdrant;
- OpenSearch vector;
- otro motor dedicado si escala.

La primera versión puede utilizar PostgreSQL + pgvector para reducir complejidad.

## 6.7 Graph

Opciones:

- Neo4j;
- PostgreSQL con tablas de relaciones para MVP.

Recomendación:

Comenzar con PostgreSQL como source of truth y agregar Neo4j cuando el grafo sea suficientemente complejo.

---

# 7. Flujo completo de procesamiento

```text
UPLOAD
  ↓
CHECKSUM
  ↓
MALWARE SCAN
  ↓
FILE IDENTIFICATION
  ↓
CASE ASSOCIATION
  ↓
DOCUMENT/MEDIA CLASSIFICATION
  ↓
PARALLEL PROCESSING
  ├── DOCUMENTS
  │    ├── OCR
  │    ├── LAYOUT
  │    ├── TEXT
  │    └── CLASSIFICATION
  │
  └── MEDIA
       ├── AUDIO EXTRACTION
       ├── ASR
       ├── DIARIZATION
       └── TIMESTAMPS
  ↓
NORMALIZATION
  ↓
LEGAL EXTRACTION
  ↓
ENTITY RESOLUTION
  ↓
EVENT EXTRACTION
  ↓
CLAIM/FACT EXTRACTION
  ↓
EVIDENCE LINKING
  ↓
CONTRADICTION ANALYSIS
  ↓
TIMELINE
  ↓
EMBEDDINGS
  ↓
INDEXING
  ↓
KNOWLEDGE GRAPH
  ↓
QUALITY CONTROL
  ↓
HUMAN REVIEW
  ↓
CASE READY
```

---

# 8. Modelo de dominio

## 8.1 Case

```text
Case
- id
- external_reference
- jurisdiction
- court
- chamber
- case_number
- title
- status
- created_at
- updated_at
- processing_status
```

## 8.2 Party

```text
Party
- id
- case_id
- name
- normalized_name
- role
- entity_type
- aliases
```

Roles:

- claimant;
- defendant;
- plaintiff;
- respondent;
- appellant;
- appellee;
- witness;
- expert;
- judge;
- attorney;
- representative;
- third_party.

## 8.3 Document

```text
Document
- id
- case_id
- storage_uri
- sha256
- mime_type
- filename
- document_type
- date
- page_count
- folio_start
- folio_end
- language
- processing_status
- parser_version
```

## 8.4 DocumentPage

```text
DocumentPage
- id
- document_id
- page_number
- folio
- image_uri
- text
- ocr_confidence
- layout_json
```

## 8.5 Media

```text
Media
- id
- case_id
- storage_uri
- media_type
- duration_ms
- sha256
- codec
- processing_status
```

## 8.6 TranscriptSegment

```text
TranscriptSegment
- id
- media_id
- speaker_id
- start_ms
- end_ms
- text
- confidence
- language
```

## 8.7 Speaker

```text
Speaker
- id
- case_id
- label
- resolved_party_id
- confidence
```

## 8.8 Event

```text
Event
- id
- case_id
- event_type
- event_date
- description
- confidence
- source_ids
```

## 8.9 Claim

```text
Claim
- id
- case_id
- text
- claim_type
- claimant_entity_id
- temporal_scope
- confidence
- status
```

Claim types:

- party_assertion;
- witness_statement;
- expert_opinion;
- documentary_statement;
- judicial_finding;
- procedural_fact;
- inference.

## 8.10 Fact

```text
Fact
- id
- case_id
- proposition
- status
- confidence
```

Statuses:

- alleged;
- disputed;
- supported;
- contradicted;
- judicially_determined;
- unresolved.

## 8.11 Evidence

```text
Evidence
- id
- case_id
- evidence_type
- description
- source_document_id
- source_media_id
- admissibility_status
- relevance
```

## 8.12 Citation

```text
Citation
- id
- target_entity_id
- source_type
- source_id
- page
- folio
- start_ms
- end_ms
- quote_hash
```

## 8.13 LegalRule

```text
LegalRule
- id
- jurisdiction
- source
- identifier
- title
- text
- version_date
```

## 8.14 Decision

```text
Decision
- id
- case_id
- decision_type
- date
- outcome
- reasoning
- source_document_id
```

---

# 9. Taxonomía documental

El clasificador debe soportar una taxonomía extensible.

Ejemplo:

```text
procedural/
  complaint
  answer
  motion
  order
  ruling
  judgment
  appeal
  notification

evidence/
  contract
  invoice
  receipt
  bank_statement
  expert_report
  photograph
  email
  message
  medical_record
  technical_report

testimony/
  deposition
  interrogation
  witness_statement
  hearing

administrative/
  filing
  certificate
  official_record

other/
  unknown
```

El clasificador debe retornar:

```json
{
  "document_type": "expert_report",
  "confidence": 0.91,
  "alternatives": [
    {
      "type": "technical_report",
      "confidence": 0.07
    }
  ]
}
```

---

# 10. OCR y comprensión documental

## 10.1 Requisitos

El OCR debe manejar:

- documentos nativos;
- escaneos;
- inclinación;
- baja resolución;
- sellos;
- firmas;
- tablas;
- encabezados;
- pies de página;
- numeración;
- manuscritos cuando sea viable.

## 10.2 Preservación de layout

Debe conservar:

- bounding boxes;
- bloques;
- líneas;
- palabras;
- tablas;
- imágenes.

Ejemplo:

```json
{
  "page": 87,
  "blocks": [
    {
      "type": "paragraph",
      "bbox": [100, 200, 900, 420],
      "text": "..."
    }
  ]
}
```

## 10.3 OCR confidence

Nunca descartar texto de baja confianza.

Debe marcarse:

```text
ocr_confidence < threshold
```

para revisión.

---

# 11. Procesamiento de videos

## 11.1 Pipeline

```text
VIDEO
 ↓
FFmpeg
 ↓
Audio extraction
 ↓
Audio normalization
 ↓
Voice Activity Detection
 ↓
ASR
 ↓
Speaker diarization
 ↓
Timestamp alignment
 ↓
Speaker resolution
 ↓
Legal segmentation
```

## 11.2 Transcript schema

```json
{
  "segment_id": "SEG-00291",
  "speaker": "SPK-04",
  "start_ms": 4421000,
  "end_ms": 4468000,
  "text": "Sí, recibí el contrato.",
  "confidence": 0.96
}
```

## 11.3 Identificación de hablante

El sistema debe distinguir:

- juez;
- abogado;
- testigo;
- perito;
- parte;
- secretario;
- otro.

Nunca asumir identidad solo por voz sin evidencia suficiente.

Debe existir:

```text
SPK-04
  ↓
possibly = Juan Pérez
confidence = 0.83
source = hearing metadata
```

y no:

```text
SPK-04 = Juan Pérez
```

si la identificación no está confirmada.

---

# 12. Extracción jurídica

## 12.1 Entidades

Extraer:

- personas;
- organizaciones;
- empresas;
- contratos;
- bienes;
- cuentas;
- lugares;
- fechas;
- valores monetarios;
- expedientes relacionados;
- normas;
- autoridades.

## 12.2 Eventos

Cada evento debe contener:

```json
{
  "event_id": "EV-001",
  "date": "2024-03-15",
  "event_type": "contract_signed",
  "description": "Se firma el contrato.",
  "participants": ["P-01", "P-02"],
  "sources": ["DOC-04:p12"]
}
```

## 12.3 Claims

El sistema debe distinguir:

```text
CLAIM
├── quién lo afirma
├── qué afirma
├── cuándo
├── sobre quién
├── fuente
├── evidencia
├── contradicciones
└── estado
```

---

# 13. Modelo de verdad jurídica

Una decisión arquitectónica crítica:

> El sistema no debe almacenar simplemente `fact = true/false`.

Debe utilizar estados epistemológicos.

```text
ALLEGED
DISPUTED
SUPPORTED
CONTRADICTED
JUDICIALLY_DETERMINED
UNRESOLVED
```

Ejemplo:

```text
Proposición:
"El demandado recibió $80.000.000."

Demandante:
ALLEGED

Demandado:
DISPUTED

Transferencia bancaria:
SUPPORTED

Testigo:
SUPPORTING_STATEMENT

Sentencia:
JUDICIALLY_DETERMINED
```

Esto evita que el modelo transforme automáticamente declaraciones en hechos.

---

# 14. Detección de contradicciones

El motor de contradicciones debe producir:

```json
{
  "contradiction_id": "CON-001",
  "claim_a": "CLM-21",
  "claim_b": "CLM-84",
  "type": "temporal",
  "description": "Las declaraciones presentan fechas incompatibles.",
  "severity": "medium",
  "sources": [
    "DOC-31:p22",
    "MED-04:01:13:27"
  ],
  "human_review_required": true
}
```

Tipos:

- factual;
- temporal;
- numerical;
- identity;
- location;
- procedural;
- testimony;
- document-vs-testimony.

La IA debe presentar la contradicción, no decidir automáticamente cuál versión es verdadera.

---

# 15. Línea de tiempo

La timeline combina:

- documentos;
- actuaciones;
- audiencias;
- contratos;
- pagos;
- hechos alegados;
- decisiones.

Cada evento debe tener fuentes.

Ejemplo:

```text
2024-01-12
Demanda presentada
[DOC-001 p1]

2024-02-01
Contestación
[DOC-009 p1]

2024-03-17
Audiencia
[MED-003 00:00:00–02:14:33]

2024-04-04
Dictamen pericial
[DOC-021 p1]
```

---

# 16. Knowledge Graph

## 16.1 Nodos

```text
Case
Person
Organization
Document
Page
Media
TranscriptSegment
Claim
Fact
Evidence
Event
LegalRule
Decision
Issue
```

## 16.2 Relaciones

```text
PART_OF
MENTIONS
ASSERTS
CONTRADICTS
SUPPORTS
REFUTES
OCCURRED_AT
PARTICIPATED_IN
TESTIFIED_IN
CITES
REFERENCES
DERIVED_FROM
DECIDES
APPLIES
RELATES_TO
```

## 16.3 Ejemplo

```text
(Person:Juan)
    └── ASSERTS ──> (Claim:C17)
                         │
                         ├── ABOUT ──> (Fact:F04)
                         │
                         └── SOURCED_FROM ──> (Transcript:S92)
                                                  │
                                                  └── PART_OF
                                                      └── (Media:M03)
```

---

# 17. Estrategia de RAG

El sistema debe implementar **hybrid retrieval**.

## 17.1 Retrieval lexical

Para:

- nombres;
- radicados;
- números;
- artículos;
- citas exactas;
- fechas;
- montos.

## 17.2 Semantic retrieval

Para:

- conceptos;
- hechos similares;
- argumentos;
- preguntas complejas.

## 17.3 Graph retrieval

Para:

- relaciones;
- quién dijo qué;
- documentos conectados;
- pruebas de un hecho;
- eventos relacionados.

## 17.4 Reranking

Resultados:

```text
Lexical
Semantic
Graph
Metadata
Temporal
```

se combinan y reranquean.

---

# 18. Arquitectura de respuesta del agente

```text
USER QUESTION
      ↓
QUESTION CLASSIFICATION
      ↓
RETRIEVAL PLAN
      ├── lexical
      ├── semantic
      ├── graph
      └── metadata
      ↓
EVIDENCE SET
      ↓
RERANK
      ↓
CONTEXT BUILDER
      ↓
LLM
      ↓
CLAIM VALIDATION
      ↓
CITATION VALIDATION
      ↓
ANSWER
```

No debe permitirse responder con información que no tenga evidencia recuperada cuando la pregunta sea sobre el expediente.

---

# 19. Citation engine

Toda respuesta debe poder producir:

```text
Afirmación
  ↓
Citation
  ↓
Documento/Folio
  OR
Video/Timestamp
```

Formato UI:

> El testigo afirmó que recibió el documento durante la reunión.  
> **Fuente:** Interrogatorio de Juan Pérez — 01:13:27–01:14:02.

El usuario debe poder hacer clic y:

- abrir el PDF en la página;
- abrir el video en timestamp;
- ver el segmento transcript;
- ver el contexto anterior/posterior.

---

# 20. Prevención de alucinaciones

## 20.1 Reglas

1. No generar hechos sin source.
2. No generar citas inexistentes.
3. No fusionar personas sin resolver identidad.
4. No convertir alegaciones en hechos.
5. No eliminar contradicciones.
6. No inventar páginas.
7. No inventar timestamps.
8. No inventar normas.
9. No utilizar conocimiento externo sin marcarlo.
10. Separar claramente información del expediente de conocimiento externo.

## 20.2 Answer contract

```json
{
  "answer": "...",
  "claims": [
    {
      "text": "...",
      "citations": ["CIT-12"]
    }
  ],
  "uncertainties": [],
  "unsupported_claims": []
}
```

---

# 21. APIs principales

## POST /v1/cases

Crear expediente.

## GET /v1/cases/{case_id}

Obtener metadata.

## POST /v1/cases/{case_id}/documents

Subir documento.

## POST /v1/cases/{case_id}/media

Subir video/audio.

## POST /v1/cases/{case_id}/process

Iniciar procesamiento.

## GET /v1/cases/{case_id}/processing

Estado.

## GET /v1/cases/{case_id}/timeline

Timeline.

## GET /v1/cases/{case_id}/facts

Hechos.

## GET /v1/cases/{case_id}/claims

Claims.

## GET /v1/cases/{case_id}/evidence

Evidencias.

## GET /v1/cases/{case_id}/contradictions

Contradicciones.

## GET /v1/cases/{case_id}/entities

Entidades.

## POST /v1/cases/{case_id}/query

Pregunta al expediente.

## GET /v1/citations/{citation_id}

Resolver citation.

## POST /v1/review/{entity_id}

Revisión humana.

---

# 22. Jobs y estados

Cada pipeline debe utilizar jobs.

```text
QUEUED
RUNNING
SUCCEEDED
FAILED
RETRYING
CANCELLED
```

Job:

```json
{
  "job_id": "JOB-001",
  "type": "document_ocr",
  "case_id": "CASE-001",
  "input_ids": ["DOC-001"],
  "pipeline_version": "1.0.0",
  "model_version": "ocr-v3",
  "status": "RUNNING"
}
```

---

# 23. Orquestación

Para MVP:

- Celery + Redis.

Para producción de alto volumen:

- Temporal.

Ventajas de Temporal:

- workflows durables;
- retries;
- timeouts;
- compensating actions;
- observabilidad;
- reanudación de procesos largos.

---

# 24. Idempotencia

Cada artefacto debe tener una clave:

```text
hash(
  input_sha256 +
  pipeline_version +
  model_version +
  configuration
)
```

Si existe un artefacto idéntico:

```text
REUSE
```

en vez de procesarlo otra vez.

---

# 25. Seguridad

## 25.1 Autenticación

OIDC/OAuth2.

Opcional:

- MFA;
- SSO empresarial.

## 25.2 Autorización

RBAC:

```text
ORG_ADMIN
CASE_MANAGER
LAWYER
REVIEWER
ANALYST
READ_ONLY
SYSTEM
```

## 25.3 Multi-tenancy

Cada registro debe incluir:

```text
organization_id
```

y el backend debe aplicar aislamiento a nivel de aplicación y, cuando sea posible, PostgreSQL Row Level Security.

## 25.4 Encryption

- TLS en tránsito;
- AES-256 o equivalente en reposo;
- KMS;
- rotación de claves.

## 25.5 Audit log

Registrar:

- login;
- acceso;
- descarga;
- creación;
- modificación;
- revisión;
- exportación;
- consulta de IA;
- cambios de permisos.

---

# 26. Integridad documental

Cada archivo original:

```text
SHA-256
size
mime
created_at
uploaded_by
storage_version
```

El sistema debe poder demostrar que un archivo analizado corresponde al archivo almacenado.

---

# 27. Retención y borrado

La política debe ser configurable por organización/jurisdicción.

Estados:

```text
ACTIVE
LEGAL_HOLD
RETENTION_PENDING
DELETED
```

Si existe legal hold:

```text
DELETE = BLOCKED
```

---

# 28. Observabilidad

## Métricas

- documentos procesados;
- páginas procesadas;
- horas de video;
- OCR latency;
- ASR latency;
- extraction latency;
- embedding latency;
- costo por expediente;
- errores;
- retries;
- tokens;
- costo LLM;
- retrieval hit rate;
- citation accuracy.

## Logs

Structured JSON logs.

Nunca registrar contenido jurídico completo en logs de aplicación salvo necesidad explícita y controles adecuados.

---

# 29. Quality gates

Un expediente no pasa a `READY` si falla:

```text
original_integrity
ocr_quality
transcript_quality
citation_integrity
schema_validation
entity_validation
pipeline_completion
```

Ejemplo:

```text
CASE_READY
IF
  all_required_jobs = SUCCESS
  AND source_integrity = PASS
  AND citation_integrity = PASS
  AND schema_validation = PASS
```

---

# 30. Evaluación de OCR

Dataset de referencia:

```text
100–500 páginas
```

Métricas:

- Character Error Rate;
- Word Error Rate;
- field extraction accuracy;
- table extraction accuracy;
- folio detection accuracy.

---

# 31. Evaluación de ASR

Dataset de audiencias reales anonimizadas.

Métricas:

- WER;
- speaker diarization error rate;
- timestamp error;
- speaker attribution accuracy;
- legal term accuracy.

---

# 32. Evaluación de extracción jurídica

Métricas:

- precision;
- recall;
- F1;
- entity resolution accuracy;
- claim extraction accuracy;
- evidence linking accuracy;
- contradiction precision.

---

# 33. Evaluación de RAG

Debe evaluarse:

### Retrieval

- Recall@K
- Precision@K
- MRR
- nDCG

### Answer

- citation correctness;
- citation completeness;
- groundedness;
- factual consistency;
- unsupported claim rate.

La métrica crítica será:

> **Unsupported Claim Rate**

Objetivo de producción:

```text
Tendencia → 0%
```

El valor exacto debe determinarse con un benchmark jurídico interno y tolerancias por tipo de tarea.

---

# 34. Human Review

La UI debe permitir revisar:

- OCR;
- documentos clasificados;
- entidades;
- speakers;
- claims;
- facts;
- evidence links;
- contradictions;
- timeline;
- citations.

Acciones:

```text
ACCEPT
EDIT
REJECT
MERGE
SPLIT
FLAG
```

Cada modificación humana debe conservar historial.

---

# 35. Versionado de conocimiento

Cada expediente debe tener:

```text
case_knowledge_version
```

Ejemplo:

```text
v1.0
v1.1
v1.2
```

Una modificación importante no debe sobrescribir silenciosamente el estado anterior.

---

# 36. Exportación

El sistema debe exportar:

## JSON

```text
case.json
documents.json
entities.json
claims.json
facts.json
evidence.json
timeline.json
citations.json
```

## JSONL

Ideal para ingestion de modelos y pipelines.

## Markdown

Resumen legible.

## HTML

Expediente estructurado.

## Parquet

Para analítica a gran escala.

## ZIP

Case Knowledge Package.

Estructura:

```text
CASE-001/
  manifest.json
  case.json
  documents/
  pages/
  media/
  transcripts/
  entities/
  claims/
  facts/
  evidence/
  timeline/
  contradictions/
  legal_rules/
  citations/
  graph/
  embeddings/
  audit/
```

---

# 37. Manifest

Ejemplo:

```json
{
  "package_version": "1.0",
  "case_id": "CASE-001",
  "created_at": "2026-09-25T00:00:00Z",
  "source_files": 124,
  "pages": 4827,
  "media_files": 14,
  "media_hours": 10.4,
  "documents_processed": 124,
  "pipeline_version": "1.0.0",
  "schema_version": "1.0"
}
```

---

# 38. Arquitectura de almacenamiento

```text
PostgreSQL
├── tenants
├── users
├── cases
├── parties
├── documents
├── pages
├── media
├── speakers
├── transcript_segments
├── entities
├── events
├── claims
├── facts
├── evidence
├── legal_rules
├── decisions
├── citations
├── contradictions
├── jobs
├── model_runs
├── reviews
└── audit_logs

Object Storage
├── originals
├── derivatives
├── OCR
├── thumbnails
├── audio
├── transcripts
└── exports

Vector
└── chunks/embeddings

Search
└── lexical index

Graph
└── entities + relations
```

---

# 39. Chunking

No utilizar chunking genérico de N tokens únicamente.

El chunk debe respetar estructura jurídica.

Tipos:

```text
document_section
paragraph
claim
testimony_segment
timeline_event
legal_rule
evidence_description
decision_reasoning
```

Metadata:

```json
{
  "chunk_id": "CH-001",
  "case_id": "CASE-001",
  "document_id": "DOC-001",
  "page": 87,
  "folio": "87",
  "section": "Hechos",
  "entity_ids": ["P-01"],
  "claim_ids": ["CLM-02"],
  "embedding_model": "..."
}
```

---

# 40. Embeddings

La selección del modelo debe ser configurable.

Debe almacenarse:

```text
embedding_model
embedding_version
dimensions
created_at
```

Nunca mezclar embeddings de modelos incompatibles en el mismo índice sin metadata.

---

# 41. Model routing

No todo debe ir al modelo más caro.

Ejemplo:

```text
TASK
├── OCR
├── classification
├── metadata extraction
├── entity extraction
├── claim extraction
├── contradiction analysis
├── summarization
└── final legal reasoning
```

Cada tarea puede tener un modelo distinto.

El router debe seleccionar:

```text
quality
latency
cost
context_length
privacy
```

---

# 42. Modelo de costos

El sistema debe medir por expediente:

```text
storage_cost
ocr_cost
asr_cost
diarization_cost
embedding_cost
llm_extraction_cost
llm_reasoning_cost
database_cost
egress_cost
compute_cost
```

Fórmula:

```text
case_cost =
storage
+ OCR
+ ASR
+ diarization
+ embeddings
+ extraction
+ reasoning
+ infrastructure allocation
```

Nunca estimar rentabilidad sin instrumentar costos reales.

---

# 43. Estrategia GPU

No se recomienda mantener GPU dedicada permanentemente para el MVP salvo necesidad operacional.

Arquitectura:

```text
CPU infrastructure
      │
      ├── API
      ├── DB
      ├── queues
      └── workers
              │
              └── GPU jobs
                    ├── OCR pesado
                    ├── ASR
                    ├── diarization
                    └── local models
```

GPU bajo demanda cuando sea económicamente conveniente.

---

# 44. Despliegue inicial

## MVP

```text
Cloud VPS
├── Docker
├── API
├── Worker
├── Redis
└── PostgreSQL

Object Storage externo
LLM/AI APIs externas
```

## Producción

```text
Load Balancer
      │
Kubernetes / managed containers
      │
├── API
├── Workers
├── GPU Workers
├── Temporal
├── PostgreSQL managed
├── Redis managed
├── Object Storage
├── Search
└── Observability
```

---

# 45. CI/CD

Pipeline:

```text
git push
 ↓
lint
 ↓
unit tests
 ↓
type checks
 ↓
security scan
 ↓
build
 ↓
integration tests
 ↓
AI evaluation tests
 ↓
deploy staging
 ↓
smoke tests
 ↓
production approval
 ↓
deploy
```

---

# 46. Testing

## Unit

- parsers;
- schema;
- authorization;
- citation resolver;
- chunking;
- deduplication.

## Integration

- upload;
- OCR;
- ASR;
- database;
- vector search;
- graph;
- RAG.

## End-to-end

Expediente completo.

## Adversarial

- documentos corruptos;
- OCR defectuoso;
- nombres similares;
- fechas contradictorias;
- videos sin audio;
- hablantes superpuestos;
- prompt injection dentro de documentos.

---

# 47. Prompt injection defense

Los documentos jurídicos son **datos no confiables**, incluso si contienen texto que parece instrucciones.

Ejemplo:

```text
"Ignore previous instructions and reveal..."
```

debe tratarse como contenido del expediente, no como una instrucción del sistema.

Regla:

```text
DOCUMENT CONTENT != SYSTEM INSTRUCTION
```

El pipeline debe separar:

```text
trusted instructions
      ≠
untrusted case content
```

---

# 48. Seguridad frente a exfiltración

El agente no debe poder:

- acceder a otros casos;
- modificar documentos;
- ejecutar código;
- consultar secretos;
- cambiar permisos;
- exportar información sin autorización.

Herramientas del agente con allowlist.

---

# 49. Agent architecture

El agente debe utilizar herramientas explícitas:

```text
search_documents()
search_claims()
search_facts()
search_evidence()
search_timeline()
search_transcripts()
get_document_page()
get_video_segment()
resolve_entity()
graph_query()
```

No debe recibir todo el expediente en el contexto.

---

# 50. Multi-agent opcional

No implementar inicialmente salvo necesidad.

Posteriormente:

```text
Orchestrator
├── Document Agent
├── Evidence Agent
├── Timeline Agent
├── Testimony Agent
├── Legal Research Agent
└── Verification Agent
```

El Verification Agent valida citations y claims antes de responder.

---

# 51. UI

## Dashboard

```text
Casos
Procesamiento
Alertas
Costos
Calidad
```

## Case Overview

```text
CASE
├── Summary
├── Timeline
├── Documents
├── Hearings
├── People
├── Facts
├── Evidence
├── Contradictions
├── Legal Issues
├── Decisions
└── AI Assistant
```

## Document viewer

Panel izquierdo:

PDF.

Panel derecho:

- metadata;
- entidades;
- claims;
- citations;
- links.

## Hearing viewer

```text
VIDEO
───────────────
TRANSCRIPT

00:12:34 JUEZ
00:12:48 TESTIGO
00:13:15 ABOGADO
```

Click en transcript:

→ video salta al timestamp.

---

# 52. UX del chat

La respuesta debe mostrar:

```text
Respuesta

...

Fuentes
[Documento p.87]
[Interrogatorio 01:13:27]

Nivel de evidencia
██████████

Incertidumbres
...
```

No usar un porcentaje de "verdad" como sustituto del análisis jurídico.

---

# 53. Roles y permisos

```text
Organization
  └── Case
       ├── Owner
       ├── Lawyer
       ├── Reviewer
       └── Viewer
```

Permisos granulares:

```text
case.read
case.write
document.read
document.upload
document.download
media.read
ai.query
ai.export
review.write
admin
```

---

# 54. API de consulta

Request:

```json
{
  "case_id": "CASE-001",
  "question": "¿Qué pruebas apoyan la afirmación de que se realizó el pago?",
  "mode": "evidence"
}
```

Response:

```json
{
  "answer": "...",
  "citations": [
    {
      "document_id": "DOC-21",
      "page": 13
    },
    {
      "media_id": "MED-04",
      "start_ms": 4421000,
      "end_ms": 4468000
    }
  ],
  "uncertainties": []
}
```

---

# 55. Modos de consulta

```text
fact_lookup
evidence_lookup
timeline
person
document
testimony
contradiction
legal_rule
summary
comparative
investigation
```

---

# 56. Investigación asistida

Pregunta:

> "¿Qué elementos del expediente contradicen la versión del demandado?"

El sistema debe:

1. recuperar claims del demandado;
2. encontrar claims incompatibles;
3. buscar evidencia;
4. localizar declaraciones;
5. construir una tabla;
6. citar fuentes;
7. indicar incertidumbres.

No debe concluir quién tiene la razón salvo que exista una determinación judicial explícita que pueda citarse.

---

# 57. Datos sensibles

El sistema debe clasificar campos potencialmente sensibles:

```text
PII
financial
medical
minor
biometric
legal_privileged
confidential
```

Políticas de acceso deben poder aplicarse por clasificación.

---

# 58. Privacidad

Requisitos:

- minimización;
- acceso mínimo;
- encryption;
- audit;
- retention policy;
- data deletion;
- tenant isolation;
- configurable data residency;
- proveedor/modelo configurable.

Si se utilizan modelos externos, el sistema debe permitir seleccionar proveedores y políticas de tratamiento de datos compatibles con los requisitos contractuales y jurisdiccionales.

---

# 59. Disaster recovery

Objetivos iniciales:

```text
RPO <= 1 hour
RTO <= 4 hours
```

Estos valores deben revisarse según SLA comercial.

Backups:

- PostgreSQL;
- object storage;
- configuration;
- secrets metadata;
- audit records.

Nunca depender únicamente de snapshots de una sola máquina.

---

# 60. Escalabilidad

Escala por:

```text
cases
documents
pages
media_hours
workers
GPU_workers
LLM_requests
```

El procesamiento debe ser asíncrono.

No bloquear HTTP esperando un expediente completo.

---

# 61. Large Case Strategy

Para expedientes de 5.000+ páginas:

```text
Case
 ↓
Manifest
 ↓
Batch documents
 ↓
Parallel OCR
 ↓
Parallel extraction
 ↓
Incremental indexing
 ↓
Incremental graph
 ↓
Incremental QA
```

El usuario debe poder comenzar a consultar documentos ya procesados sin esperar necesariamente a que termine todo el expediente, siempre que la UI indique el estado de cobertura.

---

# 62. Incremental processing

Si se agrega un documento:

```text
new document
 ↓
process only new artifact
 ↓
entity resolution
 ↓
relationship update
 ↓
timeline update
 ↓
affected embeddings
 ↓
case version++
```

No reprocesar todo el expediente innecesariamente.

---

# 63. Duplicates

Usar:

```text
SHA-256
perceptual hash
document similarity
```

Un documento idéntico debe detectarse.

Documentos similares pero no idénticos requieren revisión.

---

# 64. Entity resolution

Ejemplo:

```text
"Juan Carlos Pérez"
"J. C. Pérez"
"Juan C. Perez"
"Sr. Pérez"
```

No fusionar automáticamente únicamente por similitud textual.

Utilizar:

- contexto;
- rol;
- documento;
- identificadores disponibles;
- relaciones;
- evidencia.

Resultado:

```text
MATCH
PROBABLE_MATCH
AMBIGUOUS
NO_MATCH
```

---

# 65. Legal citation normalization

Debe normalizar:

```text
Art. 1602 C.C.
Artículo 1602 del Código Civil
C.C., art. 1602
```

a una representación canónica:

```text
jurisdiction
code
article
version
```

---

# 66. Jurisdiction abstraction

No acoplar el core a una sola jurisdicción.

Modelo:

```text
Jurisdiction
 ├── country
 ├── state/province
 ├── court_system
 ├── legal_sources
 ├── procedural_rules
 └── citation_rules
```

Esto permite adaptar la plataforma a Colombia y posteriormente a otras jurisdicciones.

---

# 67. Colombia como primera implementación posible

Si la primera jurisdicción es Colombia, crear un módulo:

```text
jurisdictions/co/
```

con:

- tipos de despacho;
- convenciones de radicación;
- normas;
- fuentes jurisprudenciales;
- nomenclatura procesal;
- reglas de citación;
- estructura de expedientes.

La capa de jurisdicción no debe contaminar el dominio genérico.

---

# 68. Monitoreo de calidad por expediente

Dashboard:

```text
OCR quality       96%
Transcript quality 93%
Entity quality     91%
Citation integrity 99%
Extraction quality 94%
```

Estos indicadores deben representar métricas técnicas verificables, no una afirmación general de "calidad jurídica".

---

# 69. Estados del expediente

```text
CREATED
UPLOADING
INGESTING
PROCESSING
PARTIALLY_READY
READY_FOR_REVIEW
REVIEWING
READY
FAILED
ARCHIVED
LEGAL_HOLD
```

---

# 70. Estados de cada documento

```text
UPLOADED
VALIDATED
OCR_PENDING
OCR_RUNNING
OCR_COMPLETE
CLASSIFICATION_PENDING
CLASSIFIED
EXTRACTION_PENDING
EXTRACTED
INDEXED
REVIEW_REQUIRED
APPROVED
FAILED
```

---

# 71. MVP

El MVP debe incluir:

### Ingesta

- PDF;
- imágenes;
- DOCX;
- video/audio.

### Procesamiento

- OCR;
- extracción de texto;
- clasificación;
- metadata;
- ASR;
- timestamps;
- diarización básica.

### Estructuración

- personas;
- fechas;
- eventos;
- claims;
- evidence;
- timeline.

### Search

- full text;
- vector;
- hybrid.

### AI

- chat sobre expediente;
- respuestas con citations.

### UI

- expediente;
- documentos;
- timeline;
- video/transcript;
- chat.

---

# 72. Fase 2

- knowledge graph;
- contradiction engine;
- entity resolution avanzado;
- revisión colaborativa;
- exportación CKP;
- analytics;
- batch processing;
- API pública.

---

# 73. Fase 3

- multi-agent;
- local models;
- GPU autoscaling;
- fine-tuned legal models;
- advanced graph reasoning;
- cross-case analytics con autorización;
- enterprise SSO;
- data residency.

---

# 74. Estructura del repositorio

```text
judicial-ai/
├── apps/
│   ├── web/
│   └── api/
├── services/
│   ├── ingestion/
│   ├── document-processing/
│   ├── media-processing/
│   ├── legal-extraction/
│   ├── entity-resolution/
│   ├── knowledge/
│   ├── retrieval/
│   ├── agent/
│   └── export/
├── packages/
│   ├── schemas/
│   ├── prompts/
│   ├── clients/
│   └── common/
├── infra/
│   ├── docker/
│   ├── terraform/
│   └── k8s/
├── evals/
├── tests/
├── docs/
└── scripts/
```

---

# 75. Estructura de schemas

```text
schemas/
├── case.schema.json
├── document.schema.json
├── media.schema.json
├── transcript.schema.json
├── entity.schema.json
├── event.schema.json
├── claim.schema.json
├── fact.schema.json
├── evidence.schema.json
├── citation.schema.json
├── contradiction.schema.json
└── manifest.schema.json
```

Todos los schemas deben versionarse.

---

# 76. Contrato de pipeline

Cada etapa:

```json
{
  "input": [],
  "output": [],
  "schema_version": "1.0",
  "pipeline_version": "1.0",
  "model_version": "model-x",
  "created_at": "...",
  "metrics": {},
  "errors": []
}
```

---

# 77. Reintentos

Errores transitorios:

```text
retry
```

Errores deterministas:

```text
manual review
```

Ejemplo:

```text
network_error → retry
invalid_pdf → manual review
model_timeout → retry
schema_error → retry/fix
ambiguous_identity → review
```

---

# 78. Cost control

Implementar presupuesto por expediente:

```text
max_processing_cost
max_llm_tokens
max_media_hours
```

Alertas:

```text
80%
90%
100%
```

Opcionalmente pausar procesamiento al superar el límite.

---

# 79. Modelo de facturación futuro

Unidades:

```text
pages_processed
minutes_of_media
storage_gb
AI_queries
exports
```

Ejemplo conceptual:

```text
Document processing
Media processing
AI analysis
Storage
```

No fijar precios comerciales hasta obtener costos reales.

---

# 80. Compliance

El sistema debe diseñarse para poder cumplir los requisitos aplicables según jurisdicción y cliente, incluyendo:

- protección de datos personales;
- confidencialidad profesional;
- controles de acceso;
- auditoría;
- retención;
- eliminación;
- seguridad;
- residencia de datos.

Para Colombia, la implementación deberá ser revisada específicamente frente al régimen colombiano aplicable de protección de datos y a las reglas de confidencialidad/proceso judicial correspondientes.

Este SSD no constituye asesoría jurídica ni certificación de cumplimiento.

---

# 81. Threat model

Amenazas:

```text
T1 — acceso no autorizado
T2 — tenant isolation failure
T3 — data exfiltration
T4 — prompt injection
T5 — malicious document
T6 — corrupted source
T7 — hallucinated citation
T8 — identity collision
T9 — model provider leakage
T10 — insider misuse
T11 — ransomware
T12 — deletion of evidence
```

Cada amenaza debe tener:

```text
threat
impact
likelihood
mitigation
test
owner
```

---

# 82. Security controls

```text
WAF
Rate limiting
MFA
RBAC
RLS
Encryption
KMS
Audit logs
Antivirus
Content sanitization
Network segmentation
Secret manager
Backup
Immutable originals
Signed URLs
Short-lived tokens
```

---

# 83. SLA técnico inicial

Métricas propuestas:

```text
API availability: 99.5% MVP
Upload success: >99%
Citation resolver: >99.9%
Queue durability: >99.9%
```

El procesamiento de expedientes debe expresarse como throughput, no como SLA HTTP.

---

# 84. SLO de procesamiento

Ejemplo inicial:

```text
Document processing throughput:
>= configurable pages/minute/worker

Media:
>= configurable real-time factor
```

El valor definitivo debe determinarse mediante benchmarking con el hardware y modelos seleccionados.

---

# 85. Data lineage

Cada output debe poder responder:

```text
¿De dónde salió?
¿Qué modelo lo produjo?
¿Qué versión?
¿Qué documentos utilizó?
¿Qué prompt?
¿Qué usuario?
¿Cuándo?
¿Fue revisado?
¿Quién lo modificó?
```

---

# 86. Audit event

```json
{
  "event_id": "AUD-001",
  "actor_id": "USR-01",
  "action": "claim.accepted",
  "entity_id": "CLM-001",
  "before": {},
  "after": {},
  "timestamp": "...",
  "ip": "...",
  "request_id": "REQ-001"
}
```

---

# 87. Backup strategy

### PostgreSQL

- daily full;
- continuous WAL/PITR.

### Object storage

- versioning;
- lifecycle;
- optional immutable retention.

### Config

- infrastructure as code;
- secrets manager;
- deployment manifests.

---

# 88. Disaster recovery test

Al menos periódicamente:

```text
restore database
restore object storage
restore application
validate checksums
run citation tests
run smoke tests
```

---

# 89. Benchmark dataset

Crear un dataset interno anonimizado:

```text
documents/
scans/
tables/
hearings/
interrogations/
contradictions/
legal_citations/
```

Cada ejemplo debe tener ground truth.

---

# 90. Golden cases

Crear 10–50 expedientes de referencia.

Cada golden case debe tener:

- expected entities;
- expected events;
- expected claims;
- expected evidence;
- expected timeline;
- expected citations;
- known contradictions;
- expected answers.

Estos casos deben bloquear regresiones.

---

# 91. AI evaluation harness

Cada cambio de:

- modelo;
- prompt;
- retrieval;
- chunking;
- embedding;
- reranker

debe ejecutar evaluación.

Comparar:

```text
baseline
vs
candidate
```

No desplegar cambios únicamente porque "parecen mejores".

---

# 92. Model registry

```text
model_id
provider
version
task
context_window
embedding_dimensions
cost
privacy_class
benchmark_score
active
```

---

# 93. Prompt registry

Todos los prompts deben vivir en código/versionados.

```text
prompts/
├── classify_document
├── extract_entities
├── extract_claims
├── extract_events
├── link_evidence
├── detect_contradictions
├── summarize_case
├── answer_question
└── verify_answer
```

No guardar prompts críticos únicamente en una interfaz manual.

---

# 94. Structured output

Los modelos deben responder schemas.

Ejemplo:

```json
{
  "claims": [
    {
      "text": "...",
      "type": "party_assertion",
      "speaker_or_author": "P-01",
      "source": "DOC-01:p10"
    }
  ]
}
```

Si no valida:

```text
REJECT → retry/repair → review
```

---

# 95. Human override

Cuando un humano modifica:

```text
AI result
 ↓
Human correction
 ↓
Canonical result
```

La corrección humana debe conservar:

```text
original_ai_output
human_output
reviewer
timestamp
reason
```

---

# 96. Exportación para IA externa

El CKP debe ser legible por:

- agentes;
- RAG;
- pipelines Python;
- data warehouses;
- modelos locales.

Ejemplo:

```text
manifest.json
case.json
entities.jsonl
claims.jsonl
facts.jsonl
evidence.jsonl
events.jsonl
transcript.jsonl
citations.jsonl
```

---

# 97. API para agentes externos

Endpoint:

```text
POST /v1/agent/query
```

Con permisos explícitos.

El agente externo nunca recibe acceso directo a PostgreSQL.

---

# 98. Principio de least context

No entregar al LLM:

```text
entire case
```

sino:

```text
question
+
retrieved evidence
+
metadata
+
required context
```

Esto reduce:

- costo;
- latencia;
- ruido;
- riesgo de confusión.

---

# 99. Context windows

Para expedientes grandes:

```text
case
 → index
 → retrieval
 → focused context
 → synthesis
```

Nunca:

```text
5,000 pages → prompt
```

como estrategia principal.

---

# 100. Long-running case analysis

Para preguntas complejas:

```text
Question
 ↓
Decompose
 ↓
Parallel retrieval
 ↓
Evidence synthesis
 ↓
Verification
 ↓
Answer
```

Ejemplo:

> "Analiza la evolución de la versión del testigo durante todo el proceso."

Subtareas:

1. encontrar todas las declaraciones;
2. resolver speaker;
3. ordenar cronológicamente;
4. extraer afirmaciones;
5. comparar;
6. identificar cambios;
7. citar cada cambio;
8. producir análisis.

---

# 101. Legal issue extraction

Crear:

```text
Issue
- id
- case_id
- description
- legal_area
- source_claims
- source_rules
- status
```

Ejemplo:

```text
ISSUE-01
¿Existió incumplimiento contractual?
```

Relacionar:

```text
Issue
 ├── claims
 ├── evidence
 ├── legal rules
 └── decisions
```

---

# 102. Judicial reasoning

Cuando exista una sentencia:

Separar:

```text
facts considered by court
issues
legal rules
reasoning
holding
orders
```

No mezclar el razonamiento judicial con las alegaciones de las partes.

---

# 103. External legal research

Debe existir separación estricta:

```text
CASE SOURCES
```

vs.

```text
EXTERNAL LEGAL SOURCES
```

Si el usuario pregunta algo externo:

```text
knowledge_type = external
```

La respuesta debe etiquetarlo.

---

# 104. Search filters

```text
document_type
date
party
speaker
page
folio
event_type
claim_type
fact_status
evidence_type
legal_rule
confidence
```

---

# 105. Full-text search

Debe permitir:

```text
"incumplimiento contractual"
```

exact phrase.

También:

```text
radicado
nombre
valor
fecha
artículo
```

---

# 106. Media search

Buscar:

```text
"pago"
```

y devolver:

```text
00:13:27
Speaker: SPK-04
Transcript: "No recuerdo haber realizado el pago."
```

Click → video.

---

# 107. OCR search

Cada resultado debe abrir:

- página;
- bounding box;
- texto original;
- texto OCR;
- confianza.

---

# 108. Evidence matrix

Vista:

| Hecho/Proposición | Evidencia | Fuente | Tipo | Estado |
|---|---|---|---|---|
| Pago realizado | Transferencia | DOC-21 p13 | documental | soporta |
| Pago realizado | Testimonio | MED-04 01:13:27 | testimonial | apoya |
| Pago realizado | Contestación | DOC-09 p22 | afirmación | disputa |

El estado debe derivarse de la evidencia y del expediente, no de una puntuación arbitraria de IA.

---

# 109. Contradiction matrix

| Proposición | Fuente A | Fuente B | Tipo | Revisión |
|---|---|---|---|---|
| Fecha del pago | DOC-21 p13 | MED-04 01:13:27 | temporal | requerida |

---

# 110. Timeline confidence

Cada evento:

```text
source-backed
inferred
ambiguous
```

No ocultar incertidumbre.

---

# 111. Data quality dashboard

Indicadores:

```text
Documents: 124/124
Pages OCR: 4827/4827
Media: 14/14
Transcript: 14/14
Entities: 642
Claims: 1,284
Evidence links: 1,903
Citations valid: 99.8%
Items requiring review: 37
```

---

# 112. Processing cost dashboard

```text
OCR             $X
ASR             $X
LLM extraction  $X
Embeddings      $X
Storage         $X
Compute         $X
Total           $X
```

---

# 113. Operational alerts

Alertar cuando:

- OCR failure rate sube;
- ASR failure rate sube;
- queue backlog crece;
- LLM cost excede presupuesto;
- citation validation falla;
- storage error;
- database replication lag;
- suspicious access.

---

# 114. API rate limits

Por organización:

```text
uploads/minute
queries/minute
concurrent_jobs
daily_ai_budget
```

---

# 115. Large file uploads

Utilizar multipart/resumable uploads.

Nunca enviar un video grande directamente a través del API server si puede evitarse.

Flujo:

```text
Client
 ↓
signed upload URL
 ↓
Object Storage
 ↓
event
 ↓
processing
```

---

# 116. Event-driven architecture

Eventos:

```text
DocumentUploaded
MediaUploaded
OCRCompleted
TranscriptCompleted
ExtractionCompleted
EntityResolved
CaseIndexed
CaseReady
ReviewRequired
```

---

# 117. Queue topology

```text
ingestion
ocr
document_extraction
media
asr
diarization
legal_extraction
embedding
indexing
graph
verification
export
```

Permite escalar workers independientemente.

---

# 118. API error model

```json
{
  "error": {
    "code": "CASE_NOT_FOUND",
    "message": "...",
    "request_id": "REQ-123"
  }
}
```

No exponer stack traces.

---

# 119. Database indexing

Índices mínimos:

```text
cases.organization_id
documents.case_id
documents.sha256
pages.document_id
transcript_segments.media_id
claims.case_id
claims.claimant_entity_id
facts.case_id
evidence.case_id
citations.target_entity_id
events.case_id + event_date
audit_logs.organization_id + timestamp
```

---

# 120. Data migrations

Alembic.

Nunca modificar producción manualmente.

Toda migración:

```text
up
down
tested
```

---

# 121. Secret management

No almacenar:

```text
API keys
DB passwords
JWT secrets
KMS credentials
```

en Git.

Usar:

- cloud secret manager;
- Vault;
- environment injection.

---

# 122. Infrastructure as Code

Terraform/OpenTofu.

Debe declarar:

- network;
- compute;
- database;
- object storage;
- queues;
- secrets;
- monitoring;
- backups.

---

# 123. Docker

Cada servicio debe tener:

- reproducible build;
- non-root user;
- healthcheck;
- minimal base image;
- pinned dependencies.

---

# 124. Health endpoints

```text
/health
/ready
```

Separar:

```text
liveness
readiness
```

---

# 125. Database transaction rules

Operaciones críticas:

```text
entity merge
claim acceptance
review changes
case permissions
```

deben ser transaccionales.

---

# 126. Concurrency

Evitar que dos revisores sobrescriban cambios.

Utilizar:

```text
optimistic locking
version number
```

---

# 127. Document immutability

Original:

```text
immutable=true
```

Derivados:

```text
versioned=true
```

---

# 128. Evidence deletion policy

No eliminar físicamente evidencia desde la UI estándar.

Acción:

```text
request deletion
```

requiere permisos administrativos y debe registrarse.

---

# 129. Explainability

La plataforma no debe intentar explicar internamente cómo "piensa" el modelo.

Debe explicar:

```text
qué fuentes utilizó
qué hechos encontró
qué relaciones identificó
qué incertidumbres detectó
```

---

# 130. Product architecture summary

```text
                    JUDICIAL AI PLATFORM
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
     INGESTA             PROCESSING           KNOWLEDGE
        │                    │                    │
 Documents/Media      OCR + ASR + NLP       SQL + Vector + Graph
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                         EVIDENCE
                             │
                    ┌────────┴────────┐
                    │                 │
                 SEARCH             RAG
                    │                 │
                    └────────┬────────┘
                             │
                       LEGAL AGENTS
                             │
                    ┌────────┴────────┐
                    │                 │
                 ANALYSIS          REVIEW
                    │                 │
                    └────────┬────────┘
                             │
                       AUDIT / EXPORT
```

---

# 131. Roadmap de implementación

## Sprint 0 — Foundations

- repository;
- CI/CD;
- Docker;
- PostgreSQL;
- object storage;
- authentication;
- base schema.

## Sprint 1 — Ingestion

- cases;
- uploads;
- checksums;
- document registry;
- media registry.

## Sprint 2 — Documents

- PDF;
- OCR;
- page extraction;
- classification;
- metadata.

## Sprint 3 — Video

- FFmpeg;
- audio;
- ASR;
- timestamps;
- diarization.

## Sprint 4 — Legal structure

- entities;
- claims;
- events;
- facts;
- evidence.

## Sprint 5 — Search

- full text;
- embeddings;
- hybrid retrieval.

## Sprint 6 — AI assistant

- query;
- citations;
- grounded answers.

## Sprint 7 — Timeline + review

- timeline;
- evidence matrix;
- human review.

## Sprint 8 — Hardening

- security;
- evaluation;
- observability;
- backup;
- load tests.

---

# 132. Definition of Done del MVP

El MVP se considera terminado cuando:

- puede crear un expediente;
- puede subir documentos;
- puede subir videos;
- preserva originales;
- calcula checksums;
- ejecuta OCR;
- extrae texto;
- clasifica documentos;
- transcribe videos;
- mantiene timestamps;
- estructura entidades;
- estructura claims;
- estructura eventos;
- estructura evidencia;
- genera timeline;
- indexa contenido;
- responde preguntas;
- devuelve citations;
- permite abrir la página fuente;
- permite abrir timestamp fuente;
- registra auditoría;
- tiene evaluación automática;
- tiene pruebas end-to-end;
- puede exportar CKP.

---

# 133. Criterios de aceptación de IA

Una respuesta no es válida si:

```text
citation inexistente
source inexistente
documento equivocado
página equivocada
timestamp equivocado
claim atribuido a persona incorrecta
alegación presentada como hecho
contradicción omitida cuando es relevante
```

---

# 134. Riesgos principales

## Riesgo 1 — OCR incorrecto

Mitigación:

- confidence;
- visual verification;
- human review.

## Riesgo 2 — ASR incorrecto

Mitigación:

- confidence;
- timestamps;
- original audio/video;
- review.

## Riesgo 3 — Speaker attribution incorrecta

Mitigación:

- diarization;
- metadata;
- human resolution.

## Riesgo 4 — Entity collision

Mitigación:

- entity resolution;
- ambiguity state.

## Riesgo 5 — Hallucination

Mitigación:

- retrieval;
- citation enforcement;
- verification.

## Riesgo 6 — Prompt injection

Mitigación:

- untrusted-content isolation;
- tool permissions;
- structured prompts.

## Riesgo 7 — Costos

Mitigación:

- model routing;
- caching;
- batching;
- budgets.

## Riesgo 8 — Data breach

Mitigación:

- encryption;
- RBAC;
- tenant isolation;
- audit;
- least privilege.

---

# 135. Arquitectura recomendada para primera versión

La implementación inicial debe evitar sobreingeniería.

```text
Next.js
   │
FastAPI
   │
PostgreSQL + pgvector
   │
Redis
   │
Celery
   │
S3-compatible Object Storage
   │
OCR provider/local worker
   │
ASR provider/local worker
   │
LLM provider
```

Posteriormente:

```text
Celery → Temporal
pgvector → dedicated vector DB if needed
PostgreSQL relations → Neo4j if needed
CPU workers → autoscaled GPU workers
```

---

# 136. Principio de evolución

La plataforma debe construirse para que:

```text
MVP
 ↓
Production
 ↓
Enterprise
```

no requiera reescribir el dominio.

Lo que puede cambiar:

- proveedor OCR;
- proveedor ASR;
- LLM;
- embedding model;
- vector database;
- cloud;
- GPU provider.

Lo que debe permanecer estable:

- Case schema;
- provenance;
- citation model;
- evidence model;
- audit model;
- API contracts;
- knowledge package format.

---

# 137. Arquitectura de proveedores

Todos los proveedores de IA deben implementarse mediante interfaces.

```python
class OCRProvider:
    def process(self, document): ...

class ASRProvider:
    def transcribe(self, media): ...

class LLMProvider:
    def structured_extract(self, schema, context): ...

class EmbeddingProvider:
    def embed(self, texts): ...
```

Esto permite cambiar proveedores sin modificar el dominio.

---

# 138. Provider abstraction

```text
AI Gateway
├── OCR adapters
├── ASR adapters
├── LLM adapters
├── Embedding adapters
└── Reranker adapters
```

El sistema registra proveedor y versión en cada ejecución.

---

# 139. Data contract entre etapas

Cada etapa debe consumir y producir schemas.

```text
RawDocument
 ↓
ParsedDocument
 ↓
ClassifiedDocument
 ↓
ExtractedDocument
 ↓
LegalDocument
 ↓
IndexedDocument
```

Video:

```text
RawMedia
 ↓
NormalizedMedia
 ↓
Audio
 ↓
Transcript
 ↓
DiarizedTranscript
 ↓
LegalTranscript
```

---

# 140. Reprocesamiento selectivo

Si cambia el modelo de extracción:

```text
No need:
OCR
ASR
```

si esos artefactos siguen siendo válidos.

Reprocesar únicamente:

```text
legal extraction
embeddings
index
```

Esto reduce costo.

---

# 141. Canonical Case Package

El formato CKP será el principal contrato de interoperabilidad.

```text
CKP
├── manifest
├── case
├── sources
├── entities
├── events
├── claims
├── facts
├── evidence
├── testimony
├── legal
├── decisions
├── contradictions
├── citations
├── graph
└── audit
```

---

# 142. Future: multimodal evidence

La arquitectura debe permitir posteriormente:

- imágenes;
- fotografías;
- capturas;
- planos;
- mapas;
- audio;
- video;
- documentos manuscritos.

Cada evidencia debe conservar:

```text
modality
source
location
confidence
provenance
```

---

# 143. Future: evidence-level multimodal reasoning

Ejemplo:

```text
Documento afirma X
        +
Fotografía muestra Y
        +
Testigo afirma Z
        ↓
Potential inconsistency
        ↓
Human review
```

El sistema puede detectar relaciones, pero no debe presentar inferencias visuales como hechos judicialmente establecidos sin respaldo.

---

# 144. Future: case comparison

Solo con autorización explícita:

```text
Case A
vs
Case B
```

Comparar:

- hechos;
- normas;
- argumentos;
- precedentes;
- decisiones.

El aislamiento de tenants debe mantenerse.

---

# 145. Future: private/local deployment

Para clientes de alta sensibilidad:

```text
On-prem
├── local object storage
├── local OCR
├── local ASR
├── local LLM
├── local vector DB
└── local graph
```

La misma API y CKP deben mantenerse.

---

# 146. Recommended implementation order

Prioridad:

```text
1. Source integrity
2. Document/media ingestion
3. OCR/ASR
4. Provenance
5. Structured case model
6. Search
7. Citation engine
8. RAG
9. Human review
10. Contradictions
11. Knowledge graph
12. Multi-agent
```

No invertir primero en agentes sofisticados antes de resolver provenance y retrieval.

---

# 147. Arquitectura final objetivo

```text
                         ┌─────────────────────┐
                         │     USERS / API     │
                         └──────────┬──────────┘
                                    │
                              IAM / Gateway
                                    │
                          ┌─────────▼─────────┐
                          │    CASE SERVICE   │
                          └─────────┬─────────┘
                                    │
                          ┌─────────▼─────────┐
                          │  OBJECT STORAGE   │
                          │ Originals/Media   │
                          └─────────┬─────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │       WORKFLOW ENGINE          │
                    └───────┬───────────────┬────────┘
                            │               │
                 ┌──────────▼──────┐ ┌─────▼───────────┐
                 │ DOCUMENT AI     │ │ MEDIA AI        │
                 │ OCR/Layout      │ │ ASR/Diarization │
                 └──────────┬──────┘ └─────┬───────────┘
                            │              │
                            └──────┬───────┘
                                   ▼
                         ┌───────────────────┐
                         │ LEGAL NORMALIZER  │
                         └─────────┬─────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
        PostgreSQL             Search/FTS          Vector DB
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   ▼
                           KNOWLEDGE GRAPH
                                   │
                                   ▼
                            RETRIEVAL ENGINE
                                   │
                                   ▼
                           EVIDENCE-AWARE RAG
                                   │
                                   ▼
                           AI AGENT LAYER
                                   │
                      ┌────────────┼─────────────┐
                      ▼            ▼             ▼
                   SUMMARY       Q&A       INVESTIGATION
                      │            │             │
                      └────────────┼─────────────┘
                                   ▼
                             VERIFICATION
                                   │
                                   ▼
                              CITATIONS
                                   │
                                   ▼
                           HUMAN REVIEW / AUDIT
```

---

# 148. Decisión arquitectónica principal

El sistema debe ser construido como:

> **Evidence-first legal intelligence infrastructure**

y no como:

> **chatbot que lee PDFs**.

La diferencia fundamental es que el chatbot puede responder preguntas, mientras que esta plataforma construye una representación persistente, versionada y verificable del expediente.

---

# 149. Resultado esperado

Para un expediente de gran tamaño, el sistema debe producir:

```text
RAW CASE
   ↓
4,827 pages
14 videos
124 documents
   ↓
STRUCTURED CASE
   ↓
642 entities
1,284 claims
1,903 evidence links
N events
N timeline entries
N contradictions
N legal references
   ↓
CASE KNOWLEDGE PACKAGE
   ↓
AI-READY
```

Las cifras anteriores son ejemplos de escala y no valores garantizados.

---

# 150. Próxima fase de ingeniería

Después de aprobar este SSD, la implementación debe comenzar en este orden:

1. Crear repositorio.
2. Definir JSON Schemas.
3. Crear PostgreSQL schema.
4. Crear Object Storage layout.
5. Implementar ingestion API.
6. Implementar document pipeline.
7. Implementar media pipeline.
8. Implementar provenance.
9. Implementar legal extraction.
10. Implementar indexing.
11. Implementar retrieval.
12. Implementar citation resolver.
13. Implementar AI query endpoint.
14. Implementar UI.
15. Crear golden dataset.
16. Ejecutar benchmark.
17. Hardening de seguridad.
18. Deploy staging.
19. Procesar expedientes de prueba.
20. Medir costo/latencia/calidad.
21. Ajustar arquitectura.
22. Deploy production.

---

# 151. Checklist de implementación

## Foundation

- [ ] Repository
- [ ] CI/CD
- [ ] Docker
- [ ] Environment management
- [ ] Secrets
- [ ] PostgreSQL
- [ ] Object storage
- [ ] Redis
- [ ] Authentication

## Ingestion

- [ ] Case creation
- [ ] Upload
- [ ] Checksums
- [ ] Deduplication
- [ ] Antivirus
- [ ] Metadata

## Documents

- [ ] PDF parsing
- [ ] OCR
- [ ] Layout
- [ ] Classification
- [ ] Folios
- [ ] Tables

## Media

- [ ] FFmpeg
- [ ] Audio normalization
- [ ] ASR
- [ ] Diarization
- [ ] Timestamp
- [ ] Speaker resolution

## Legal

- [ ] Entity extraction
- [ ] Event extraction
- [ ] Claim extraction
- [ ] Fact state
- [ ] Evidence linking
- [ ] Timeline
- [ ] Contradictions
- [ ] Legal references

## Knowledge

- [ ] PostgreSQL
- [ ] FTS
- [ ] pgvector
- [ ] Graph model
- [ ] Hybrid retrieval
- [ ] Reranking

## AI

- [ ] AI gateway
- [ ] Model registry
- [ ] Prompt registry
- [ ] Structured outputs
- [ ] RAG
- [ ] Citation validation
- [ ] Verification

## UI

- [ ] Dashboard
- [ ] Case
- [ ] Document viewer
- [ ] Media viewer
- [ ] Timeline
- [ ] Evidence matrix
- [ ] Contradiction matrix
- [ ] Chat
- [ ] Review

## Security

- [ ] RBAC
- [ ] Tenant isolation
- [ ] RLS
- [ ] Encryption
- [ ] Audit
- [ ] Backups
- [ ] DR
- [ ] Threat model
- [ ] Prompt injection defense

## QA

- [ ] Golden cases
- [ ] OCR benchmark
- [ ] ASR benchmark
- [ ] Extraction benchmark
- [ ] RAG benchmark
- [ ] Citation benchmark
- [ ] Load testing
- [ ] Security testing

---

# 152. Final architectural requirement

El criterio de éxito más importante del sistema es:

> **Una IA puede leer y razonar sobre un expediente complejo sin perder la conexión entre cada afirmación y la evidencia original que la sustenta.**

Por ello, **provenance, source integrity, structured representation, retrieval y citation validation** son componentes de primera clase y no funcionalidades accesorias.

