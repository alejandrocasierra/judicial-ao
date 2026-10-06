-- =====================================================================
-- 010_schema.sql — Modelo físico (SSD §8, §38). Plantilla: las variables entre llaves dobles se
-- reemplaza desde variables de entorno por el runner de migraciones.
-- =====================================================================
CREATE TABLE organizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(name) BETWEEN 2 AND 200),
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9\-]{1,62}$'),
  default_locale text NOT NULL CHECK (default_locale IN ('es','en')),
  data_residency text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  email text NOT NULL CHECK (email = lower(email) AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  full_name text NOT NULL CHECK (length(full_name) BETWEEN 2 AND 200),
  password_hash text NOT NULL,
  org_role text NOT NULL CHECK (org_role IN ('ORG_ADMIN','CASE_MANAGER','LAWYER','REVIEWER','ANALYST','READ_ONLY','SYSTEM')),
  locale text NOT NULL CHECK (locale IN ('es','en')),
  is_active boolean NOT NULL DEFAULT true,
  failed_login_attempts int NOT NULL DEFAULT 0,
  locked_until timestamptz,
  last_login_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (email)
);

CREATE TABLE refresh_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  user_id uuid NOT NULL REFERENCES users(id),
  token_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  external_reference text,
  jurisdiction text NOT NULL,
  court text,
  chamber text,
  case_number text NOT NULL,
  title text NOT NULL CHECK (length(title) BETWEEN 3 AND 300),
  status text NOT NULL DEFAULT 'CREATED' CHECK (status IN ('CREATED','UPLOADING','INGESTING','PROCESSING','PARTIALLY_READY','READY_FOR_REVIEW','REVIEWING','READY','FAILED','ARCHIVED')),
  retention_status text NOT NULL DEFAULT 'ACTIVE' CHECK (retention_status IN ('ACTIVE','LEGAL_HOLD','RETENTION_PENDING','DELETED')),
  legal_hold boolean NOT NULL DEFAULT false,
  knowledge_version text NOT NULL DEFAULT 'v1.0',
  language text NOT NULL CHECK (language IN ('es','en')),
  max_processing_cost numeric(12,2) NOT NULL,
  max_llm_tokens bigint NOT NULL,
  max_media_hours numeric(8,2) NOT NULL,
  spent_processing_cost numeric(12,2) NOT NULL DEFAULT 0,
  spent_llm_tokens bigint NOT NULL DEFAULT 0,
  version int NOT NULL DEFAULT 1,
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, jurisdiction, case_number),
  CHECK (legal_hold = (retention_status = 'LEGAL_HOLD'))
);

CREATE TABLE case_members (
  case_id uuid NOT NULL REFERENCES cases(id),
  user_id uuid NOT NULL REFERENCES users(id),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_role text NOT NULL CHECK (case_role IN ('OWNER','LAWYER','REVIEWER','VIEWER')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (case_id, user_id)
);

CREATE TABLE parties (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  name text NOT NULL,
  normalized_name text NOT NULL,
  role text NOT NULL CHECK (role IN ('claimant','defendant','plaintiff','respondent','appellant','appellee','witness','expert','judge','attorney','representative','third_party')),
  entity_type text NOT NULL CHECK (entity_type IN ('person','organization')),
  aliases text[] NOT NULL DEFAULT '{}',
  sensitivity text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  storage_uri text NOT NULL,
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes > 0),
  mime_type text NOT NULL,
  filename text NOT NULL,
  document_type text NOT NULL DEFAULT 'other/unknown',
  document_type_confidence numeric(4,3),
  document_date date,
  page_count int,
  folio_start text,
  folio_end text,
  language text,
  processing_status text NOT NULL DEFAULT 'UPLOADED' CHECK (processing_status IN ('UPLOADED','VALIDATED','OCR_PENDING','OCR_RUNNING','OCR_COMPLETE','CLASSIFICATION_PENDING','CLASSIFIED','EXTRACTION_PENDING','EXTRACTED','INDEXED','REVIEW_REQUIRED','APPROVED','FAILED')),
  parser_version text,
  immutable boolean NOT NULL DEFAULT true CHECK (immutable),
  uploaded_by uuid REFERENCES users(id),
  deletion_requested_at timestamptz,
  deletion_requested_by uuid REFERENCES users(id),
  deletion_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, sha256)
);

CREATE TABLE document_pages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  document_id uuid NOT NULL REFERENCES documents(id),
  page_number int NOT NULL CHECK (page_number >= 1),
  folio text,
  image_uri text,
  text text NOT NULL DEFAULT '',
  ocr_confidence numeric(4,3) CHECK (ocr_confidence BETWEEN 0 AND 1),
  needs_review boolean NOT NULL DEFAULT false,
  layout_json jsonb,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('{{FTS_CONFIG}}', coalesce(text,''))) STORED,
  UNIQUE (document_id, page_number)
);

CREATE TABLE media (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  storage_uri text NOT NULL,
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes > 0),
  mime_type text NOT NULL,
  filename text NOT NULL,
  title text,
  media_type text NOT NULL CHECK (media_type IN ('video','audio')),
  duration_ms bigint CHECK (duration_ms >= 0),
  codec text,
  processing_status text NOT NULL DEFAULT 'UPLOADED',
  uploaded_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, sha256)
);

CREATE TABLE speakers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  label text NOT NULL,
  speaker_role text,  -- libre: roles estándar o personalizados (juez, apoderado, perito, …)
  resolved_party_id uuid REFERENCES parties(id),
  resolution_status text NOT NULL DEFAULT 'UNRESOLVED' CHECK (resolution_status IN ('UNRESOLVED','PROBABLE','CONFIRMED')),
  resolution_source text,
  confidence numeric(4,3),
  version int NOT NULL DEFAULT 1,
  -- SSD §11.3: nunca afirmar identidad confirmada sin parte vinculada
  CHECK (resolution_status <> 'CONFIRMED' OR resolved_party_id IS NOT NULL),
  UNIQUE (case_id, label)
);

CREATE TABLE transcript_segments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  media_id uuid NOT NULL REFERENCES media(id),
  speaker_id uuid REFERENCES speakers(id),
  start_ms bigint NOT NULL CHECK (start_ms >= 0),
  end_ms bigint NOT NULL,
  text text NOT NULL,
  confidence numeric(4,3) CHECK (confidence BETWEEN 0 AND 1),
  needs_review boolean NOT NULL DEFAULT false,
  language text,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('{{FTS_CONFIG}}', coalesce(text,''))) STORED,
  CHECK (end_ms > start_ms)
);

CREATE TABLE entities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  entity_type text NOT NULL CHECK (entity_type IN ('person','organization','contract','asset','account','place','date','money','related_case','legal_rule','authority')),
  name text NOT NULL,
  normalized_name text NOT NULL,
  aliases text[] NOT NULL DEFAULT '{}',
  resolution_status text NOT NULL DEFAULT 'AMBIGUOUS' CHECK (resolution_status IN ('MATCH','PROBABLE_MATCH','AMBIGUOUS','NO_MATCH')),
  party_id uuid REFERENCES parties(id),
  attributes jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  decision_type text NOT NULL,
  decision_date date,
  outcome text,
  reasoning text,
  source_document_id uuid NOT NULL REFERENCES documents(id)
);

CREATE TABLE events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  kind text NOT NULL DEFAULT 'generic' CHECK (kind IN ('generic','procedural')),
  event_type text NOT NULL,
  subtype text,
  instance text,
  actor text,
  authority text,
  date_type text,
  procedural_effect text,
  document_id uuid REFERENCES documents(id),
  page_number int,
  event_date date,
  date_precision text NOT NULL DEFAULT 'day' CHECK (date_precision IN ('day','month','year','unknown')),
  description text NOT NULL,
  timeline_confidence text NOT NULL CHECK (timeline_confidence IN ('source_backed','inferred','ambiguous')),
  confidence numeric(4,3),
  participants uuid[] NOT NULL DEFAULT '{}'
);

CREATE TABLE claims (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  text text NOT NULL,
  claim_type text NOT NULL CHECK (claim_type IN ('party_assertion','witness_statement','expert_opinion','documentary_statement','judicial_finding','procedural_fact','inference')),
  claimant_party_id uuid REFERENCES parties(id),
  temporal_scope text,
  confidence numeric(4,3),
  review_status text NOT NULL DEFAULT 'PENDING' CHECK (review_status IN ('PENDING','ACCEPTED','EDITED','REJECTED','FLAGGED')),
  origin text NOT NULL DEFAULT 'ai' CHECK (origin IN ('ai','human')),
  original_ai_output jsonb,
  version int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE facts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  proposition text NOT NULL,
  status text NOT NULL DEFAULT 'ALLEGED' CHECK (status IN ('ALLEGED','DISPUTED','SUPPORTED','CONTRADICTED','JUDICIALLY_DETERMINED','UNRESOLVED')),
  determined_by_decision_id uuid REFERENCES decisions(id),
  confidence numeric(4,3),
  review_status text NOT NULL DEFAULT 'PENDING' CHECK (review_status IN ('PENDING','ACCEPTED','EDITED','REJECTED','FLAGGED')),
  version int NOT NULL DEFAULT 1,
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- SSD §13: nunca "determinado judicialmente" sin decisión citada
  CHECK (status <> 'JUDICIALLY_DETERMINED' OR determined_by_decision_id IS NOT NULL)
);

CREATE TABLE fact_claims (
  organization_id uuid NOT NULL REFERENCES organizations(id),
  fact_id uuid NOT NULL REFERENCES facts(id),
  claim_id uuid NOT NULL REFERENCES claims(id),
  stance text NOT NULL CHECK (stance IN ('asserts','disputes')),
  PRIMARY KEY (fact_id, claim_id)
);

CREATE TABLE evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  evidence_type text NOT NULL CHECK (evidence_type IN ('documentary','testimonial','expert','physical','digital','other')),
  description text NOT NULL,
  source_document_id uuid REFERENCES documents(id),
  source_media_id uuid REFERENCES media(id),
  admissibility_status text NOT NULL DEFAULT 'unknown' CHECK (admissibility_status IN ('unknown','admitted','rejected','pending')),
  relevance text,
  CHECK (source_document_id IS NOT NULL OR source_media_id IS NOT NULL)
);

CREATE TABLE evidence_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  evidence_id uuid NOT NULL REFERENCES evidence(id),
  fact_id uuid NOT NULL REFERENCES facts(id),
  stance text NOT NULL CHECK (stance IN ('supports','refutes','neutral')),
  UNIQUE (evidence_id, fact_id)
);

CREATE TABLE citations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  target_type text NOT NULL CHECK (target_type IN ('claim','fact','event','evidence','contradiction','decision','answer','entity')),
  target_id uuid NOT NULL,
  source_type text NOT NULL CHECK (source_type IN ('document_page','transcript_segment')),
  document_id uuid REFERENCES documents(id),
  page_number int,
  folio text,
  char_start int,
  char_end int,
  media_id uuid REFERENCES media(id),
  segment_id uuid REFERENCES transcript_segments(id),
  start_ms bigint,
  end_ms bigint,
  quote_hash char(64),
  created_at timestamptz NOT NULL DEFAULT now(),
  -- SSD §3.2 provenance: documento+página o media+timestamp, nunca a medias
  CHECK (
    (source_type = 'document_page' AND document_id IS NOT NULL AND page_number IS NOT NULL AND media_id IS NULL)
    OR
    (source_type = 'transcript_segment' AND media_id IS NOT NULL AND segment_id IS NOT NULL AND start_ms IS NOT NULL AND end_ms > start_ms AND document_id IS NULL)
  ),
  CHECK (char_start IS NULL OR (char_start >= 0 AND char_end > char_start))
);

CREATE TABLE contradictions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  claim_a_id uuid NOT NULL REFERENCES claims(id),
  claim_b_id uuid NOT NULL REFERENCES claims(id),
  contradiction_type text NOT NULL CHECK (contradiction_type IN ('factual','temporal','numerical','identity','location','procedural','testimony','document_vs_testimony')),
  description text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('low','medium','high')),
  human_review_required boolean NOT NULL DEFAULT true CHECK (human_review_required),
  review_status text NOT NULL DEFAULT 'PENDING' CHECK (review_status IN ('PENDING','ACCEPTED','EDITED','REJECTED','FLAGGED')),
  version int NOT NULL DEFAULT 1,
  CHECK (claim_a_id <> claim_b_id)
);

CREATE TABLE legal_rules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  jurisdiction text NOT NULL,
  source text NOT NULL,
  identifier text NOT NULL,
  title text,
  text text,
  version_date date,
  UNIQUE (organization_id, jurisdiction, source, identifier)
);

CREATE TABLE issues (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  description text NOT NULL,
  legal_area text,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','decided','withdrawn'))
);

CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  job_type text NOT NULL,
  input_ids uuid[] NOT NULL DEFAULT '{}',
  idempotency_key char(64) NOT NULL,
  pipeline_version text NOT NULL,
  model_version text,
  status text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','RETRYING','CANCELLED')),
  attempts int NOT NULL DEFAULT 0,
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, idempotency_key)
);

CREATE TABLE model_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid REFERENCES cases(id),
  task text NOT NULL,
  provider text NOT NULL,
  model text NOT NULL,
  prompt_id text,
  prompt_version text,
  pipeline_version text NOT NULL,
  input_hash char(64) NOT NULL,
  output jsonb,
  validation jsonb,
  tokens_in int NOT NULL DEFAULT 0,
  tokens_out int NOT NULL DEFAULT 0,
  cost numeric(12,6) NOT NULL DEFAULT 0,
  actor_id uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  entity_type text NOT NULL,
  entity_id uuid NOT NULL,
  action text NOT NULL CHECK (action IN ('ACCEPT','EDIT','REJECT','FLAG')),
  reviewer_id uuid NOT NULL REFERENCES users(id),
  reason text,
  original_output jsonb NOT NULL,
  human_output jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
  id bigserial PRIMARY KEY,
  organization_id uuid REFERENCES organizations(id),
  actor_id uuid,
  action text NOT NULL,
  entity_type text,
  entity_id text,
  before jsonb,
  after jsonb,
  ip text,
  request_id text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  prev_hash char(64),
  hash char(64)
);

CREATE TABLE chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  chunk_type text NOT NULL CHECK (chunk_type IN ('document_section','paragraph','claim','testimony_segment','timeline_event','legal_rule','evidence_description','decision_reasoning')),
  document_id uuid REFERENCES documents(id),
  page_number int,
  media_id uuid REFERENCES media(id),
  start_ms bigint,
  end_ms bigint,
  text text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  embedding vector({{EMBEDDING_DIMENSIONS}}),
  embedding_model text,
  embedding_version text,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('{{FTS_CONFIG}}', coalesce(text,''))) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (embedding IS NULL OR embedding_model IS NOT NULL)
);

CREATE TABLE graph_nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  node_type text NOT NULL CHECK (node_type IN ('Case','Person','Organization','Document','Claim','Fact','Evidence','Event','LegalRule','Decision','Issue','Speaker')),
  source_table text,
  source_id uuid,
  label text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, source_table, source_id, node_type)
);

CREATE TABLE graph_edges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  case_id uuid NOT NULL REFERENCES cases(id),
  source_node_id uuid NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
  target_node_id uuid NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
  edge_type text NOT NULL CHECK (edge_type IN ('ASSERTS','SUPPORTS','CONTRADICTS','REFUTES','CITES','PARTICIPATED_IN','TESTIFIED_IN','DECIDES','APPLIES','DERIVED_FROM','MENTIONS','ABOUT','HAS_SPEAKER','IS_PARTY')),
  provenance text NOT NULL CHECK (provenance IN ('EXTRACTED','INFERRED','AMBIGUOUS')),
  confidence numeric(4,3) CHECK (confidence >= 0 AND confidence <= 1),
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (source_node_id <> target_node_id)
);
