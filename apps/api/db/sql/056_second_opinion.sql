-- 056 - Segunda opinion por proceso/demanda.

CREATE TABLE IF NOT EXISTS second_opinion_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  audit_id uuid REFERENCES answer_audits(id) ON DELETE SET NULL,
  chat_message_id uuid REFERENCES chat_messages(id) ON DELETE SET NULL,
  responder_model_run_id uuid REFERENCES model_runs(id) ON DELETE SET NULL,
  verifier_model_id uuid REFERENCES ai_models(id) ON DELETE SET NULL,
  verifier_provider text,
  verifier_model_name text,
  status text NOT NULL DEFAULT 'QUEUED'
    CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')),
  verdict text CHECK (verdict IN ('AGREE','PARTIAL','DISAGREE','INCONCLUSIVE')),
  summary text,
  output jsonb NOT NULL DEFAULT '{}'::jsonb,
  model_run_id uuid REFERENCES model_runs(id) ON DELETE SET NULL,
  tokens_in int NOT NULL DEFAULT 0,
  tokens_out int NOT NULL DEFAULT 0,
  cost numeric(12,6) NOT NULL DEFAULT 0,
  duration_ms int,
  error_code text,
  error_detail text,
  idempotency_key char(64),
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_second_opinion_idempotency
  ON second_opinion_runs(organization_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_second_opinion_case_created
  ON second_opinion_runs(case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_second_opinion_status
  ON second_opinion_runs(status);
CREATE INDEX IF NOT EXISTS ix_second_opinion_audit
  ON second_opinion_runs(audit_id);

CREATE TABLE IF NOT EXISTS second_opinion_claims (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES second_opinion_runs(id) ON DELETE CASCADE,
  claim_index int NOT NULL,
  claim_text text NOT NULL,
  claim_hash char(64) NOT NULL,
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  status text NOT NULL DEFAULT 'SUPPORTED'
    CHECK (status IN ('SUPPORTED','UNSUPPORTED','CONTRADICTED','NEEDS_REVIEW')),
  comparison text NOT NULL DEFAULT 'UNCERTAIN'
    CHECK (comparison IN ('MATCH','MISSING','EXTRA','CONTRADICTED','UNCERTAIN')),
  reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, claim_index)
);
CREATE INDEX IF NOT EXISTS ix_second_opinion_claims_run
  ON second_opinion_claims(run_id);

CREATE TABLE IF NOT EXISTS second_opinion_findings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES second_opinion_runs(id) ON DELETE CASCADE,
  claim_index int,
  code text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
  message text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_second_opinion_findings_run
  ON second_opinion_findings(run_id, severity);
