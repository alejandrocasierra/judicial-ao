-- 055 - Legal guard: politicas, auditorias de respuesta y citas de normas.
-- Aditivo: no modifica tablas existentes salvo columnas opcionales de legal_rules.

CREATE TABLE IF NOT EXISTS legal_guard_policies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid REFERENCES cases(id) ON DELETE CASCADE,
  name text NOT NULL,
  mode text NOT NULL DEFAULT 'BALANCED' CHECK (mode IN ('STRICT','BALANCED','ADVISORY')),
  require_primary_citation boolean NOT NULL DEFAULT true,
  require_legal_norm boolean NOT NULL DEFAULT true,
  block_on_unsupported boolean NOT NULL DEFAULT true,
  block_on_numbers boolean NOT NULL DEFAULT true,
  block_on_parties boolean NOT NULL DEFAULT true,
  block_on_legal_conclusion boolean NOT NULL DEFAULT true,
  require_second_opinion boolean NOT NULL DEFAULT false,
  second_opinion_model_id uuid REFERENCES ai_models(id) ON DELETE SET NULL,
  max_unsupported_claims int NOT NULL DEFAULT 0 CHECK (max_unsupported_claims >= 0),
  min_ocr_confidence numeric(4,3) NOT NULL DEFAULT 0.55 CHECK (min_ocr_confidence BETWEEN 0 AND 1),
  min_asr_confidence numeric(4,3) NOT NULL DEFAULT 0.55 CHECK (min_asr_confidence BETWEEN 0 AND 1),
  allowed_jurisdictions text[] NOT NULL DEFAULT '{}',
  version int NOT NULL DEFAULT 1,
  is_default boolean NOT NULL DEFAULT false,
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_legal_guard_policy_default
  ON legal_guard_policies(organization_id)
  WHERE case_id IS NULL AND is_default;
CREATE UNIQUE INDEX IF NOT EXISTS ux_legal_guard_policy_case_name
  ON legal_guard_policies(organization_id, case_id, lower(name))
  WHERE case_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_legal_guard_policy_org_case
  ON legal_guard_policies(organization_id, case_id);

-- Politica global por defecto para organizaciones existentes y futuras (la API
-- tambien la crea de forma perezosa para organizaciones nuevas).
INSERT INTO legal_guard_policies
  (organization_id, name, mode, require_primary_citation, require_legal_norm,
   block_on_unsupported, block_on_numbers, block_on_parties, block_on_legal_conclusion,
   is_default)
SELECT o.id, 'Politica legal estandar', 'BALANCED', true, true, true, true, true, true, true
FROM organizations o
WHERE NOT EXISTS (
  SELECT 1 FROM legal_guard_policies p
  WHERE p.organization_id = o.id AND p.case_id IS NULL AND p.is_default
);

CREATE TABLE IF NOT EXISTS case_verification_settings (
  case_id uuid PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  policy_id uuid NOT NULL REFERENCES legal_guard_policies(id) ON DELETE RESTRICT,
  second_opinion_enabled boolean NOT NULL DEFAULT false,
  second_opinion_model_id uuid REFERENCES ai_models(id) ON DELETE SET NULL,
  auto_second_opinion text NOT NULL DEFAULT 'NEVER'
    CHECK (auto_second_opinion IN ('NEVER','ANALYSIS','ALL','STRICT_ONLY')),
  updated_by uuid REFERENCES users(id),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_case_verification_settings_policy
  ON case_verification_settings(policy_id);

CREATE TABLE IF NOT EXISTS answer_audits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  chat_message_id uuid REFERENCES chat_messages(id) ON DELETE SET NULL,
  model_run_id uuid REFERENCES model_runs(id) ON DELETE SET NULL,
  policy_id uuid REFERENCES legal_guard_policies(id) ON DELETE SET NULL,
  question text NOT NULL,
  answer_hash char(64) NOT NULL,
  answer_excerpt text,
  status text NOT NULL DEFAULT 'PASSED'
    CHECK (status IN ('PASSED','WARNING','BLOCKED','NEEDS_REVIEW','ERROR')),
  mode text NOT NULL DEFAULT 'BALANCED',
  claims_total int NOT NULL DEFAULT 0,
  claims_supported int NOT NULL DEFAULT 0,
  claims_blocked int NOT NULL DEFAULT 0,
  findings_critical int NOT NULL DEFAULT 0,
  evidence_hash char(64),
  evidence_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  pipeline_version text NOT NULL DEFAULT '',
  prompt_version text,
  duration_ms int,
  idempotency_key char(64),
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_answer_audits_idempotency
  ON answer_audits(organization_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_answer_audits_case_created
  ON answer_audits(case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_answer_audits_message
  ON answer_audits(chat_message_id);
CREATE INDEX IF NOT EXISTS ix_answer_audits_model_run
  ON answer_audits(model_run_id);
CREATE INDEX IF NOT EXISTS ix_answer_audits_status
  ON answer_audits(status);

CREATE TABLE IF NOT EXISTS answer_audit_claims (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  audit_id uuid NOT NULL REFERENCES answer_audits(id) ON DELETE CASCADE,
  claim_index int NOT NULL,
  claim_text text NOT NULL,
  claim_hash char(64) NOT NULL,
  claim_type text NOT NULL DEFAULT 'FACT'
    CHECK (claim_type IN ('FACT','LEGAL_CONCLUSION','PROCEDURAL','PARTY_ALLEGATION','INFERENCE')),
  citation_handles text[] NOT NULL DEFAULT '{}',
  status text NOT NULL CHECK (status IN ('SUPPORTED','UNSUPPORTED','CONTRADICTED','NEEDS_REVIEW')),
  grounding_score numeric(6,5),
  missing_numbers text[] NOT NULL DEFAULT '{}',
  missing_dates text[] NOT NULL DEFAULT '{}',
  missing_parties text[] NOT NULL DEFAULT '{}',
  legal_rule_ids uuid[] NOT NULL DEFAULT '{}',
  reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  auditor text NOT NULL DEFAULT 'DETERMINISTIC' CHECK (auditor IN ('DETERMINISTIC','JUDGE')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (audit_id, claim_index, auditor)
);
CREATE INDEX IF NOT EXISTS ix_answer_audit_claims_audit
  ON answer_audit_claims(audit_id, claim_index);

CREATE TABLE IF NOT EXISTS answer_audit_citations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  audit_id uuid NOT NULL REFERENCES answer_audits(id) ON DELETE CASCADE,
  citation_id uuid REFERENCES citations(id) ON DELETE SET NULL,
  valid boolean NOT NULL DEFAULT false,
  reasons text[] NOT NULL DEFAULT '{}',
  source_type text,
  source_id uuid,
  page_number int,
  segment_id uuid,
  quote_hash char(64),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_answer_audit_citations_audit
  ON answer_audit_citations(audit_id);
CREATE INDEX IF NOT EXISTS ix_answer_audit_citations_citation
  ON answer_audit_citations(citation_id);

CREATE TABLE IF NOT EXISTS answer_audit_findings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  audit_id uuid NOT NULL REFERENCES answer_audits(id) ON DELETE CASCADE,
  claim_index int,
  code text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
  message text NOT NULL,
  source_type text,
  source_id uuid,
  blocking boolean NOT NULL DEFAULT false,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_answer_audit_findings_audit_sev
  ON answer_audit_findings(audit_id, severity);
CREATE INDEX IF NOT EXISTS ix_answer_audit_findings_code
  ON answer_audit_findings(code);

CREATE TABLE IF NOT EXISTS legal_rule_citations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  legal_rule_id uuid NOT NULL REFERENCES legal_rules(id) ON DELETE RESTRICT,
  target_type text NOT NULL CHECK (target_type IN ('claim','fact','answer')),
  target_id uuid NOT NULL,
  quote text,
  version_date date,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_legal_rule_citations_case
  ON legal_rule_citations(case_id, target_type, target_id);
CREATE INDEX IF NOT EXISTS ix_legal_rule_citations_rule
  ON legal_rule_citations(legal_rule_id);

ALTER TABLE legal_rules ADD COLUMN IF NOT EXISTS official_url text;
ALTER TABLE legal_rules ADD COLUMN IF NOT EXISTS checksum char(64);
ALTER TABLE legal_rules ADD COLUMN IF NOT EXISTS verified_at timestamptz;
ALTER TABLE legal_rules ADD COLUMN IF NOT EXISTS verified_by uuid REFERENCES users(id);
ALTER TABLE legal_rules ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE legal_rules ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE UNIQUE INDEX IF NOT EXISTS ux_legal_rules_org_version
  ON legal_rules(organization_id, jurisdiction, source, identifier, version_date);
