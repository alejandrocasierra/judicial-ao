-- 057 - Hechos por parte y estado de prueba.
-- `status` sigue siendo el estado epistemico heredado; `proof_status` es el
-- estado de publicacion del ledger de hechos.

ALTER TABLE facts ADD COLUMN IF NOT EXISTS proof_status text NOT NULL DEFAULT 'ALLEGED';
ALTER TABLE facts DROP CONSTRAINT IF EXISTS facts_proof_status_check;
ALTER TABLE facts ADD CONSTRAINT facts_proof_status_check
  CHECK (proof_status IN ('ALLEGED','ADMITTED','EVIDENCE_SUPPORTED','JUDICIALLY_PROVEN','DISPUTED','REJECTED','UNRESOLVED'));

ALTER TABLE facts ADD COLUMN IF NOT EXISTS proof_basis text NOT NULL DEFAULT 'NONE';
ALTER TABLE facts DROP CONSTRAINT IF EXISTS facts_proof_basis_check;
ALTER TABLE facts ADD CONSTRAINT facts_proof_basis_check
  CHECK (proof_basis IN ('NONE','ADMISSION','EVIDENCE','JUDICIAL_DECISION'));

ALTER TABLE facts ADD COLUMN IF NOT EXISTS proven_by_decision_id uuid REFERENCES decisions(id) ON DELETE SET NULL;
ALTER TABLE facts ADD COLUMN IF NOT EXISTS proven_at timestamptz;
ALTER TABLE facts ADD COLUMN IF NOT EXISTS proven_by uuid REFERENCES users(id);
ALTER TABLE facts ADD COLUMN IF NOT EXISTS issue_id uuid REFERENCES issues(id) ON DELETE SET NULL;
ALTER TABLE facts ADD COLUMN IF NOT EXISTS fact_code text;
ALTER TABLE facts ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE facts ADD COLUMN IF NOT EXISTS is_proven boolean
  GENERATED ALWAYS AS (proof_status IN ('ADMITTED','JUDICIALLY_PROVEN')) STORED;

ALTER TABLE facts DROP CONSTRAINT IF EXISTS facts_judicially_proven_requires_decision;
ALTER TABLE facts ADD CONSTRAINT facts_judicially_proven_requires_decision
  CHECK (proof_status <> 'JUDICIALLY_PROVEN'
         OR coalesce(proven_by_decision_id, determined_by_decision_id) IS NOT NULL);

CREATE TABLE IF NOT EXISTS fact_positions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  fact_id uuid NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
  party_id uuid REFERENCES parties(id) ON DELETE SET NULL,
  side text NOT NULL CHECK (side IN ('DEMANDANTE','DEMANDADO','TERCERO','NO_ATRIBUIDO')),
  position text NOT NULL CHECK (position IN ('ALLEGES','DENIES','ADMITS','UNKNOWN')),
  source_claim_id uuid REFERENCES claims(id) ON DELETE SET NULL,
  source_citation_id uuid REFERENCES citations(id) ON DELETE SET NULL,
  confidence numeric(4,3),
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_positions_party
  ON fact_positions(fact_id, party_id) WHERE party_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_positions_unattributed
  ON fact_positions(fact_id, side, position) WHERE party_id IS NULL;
CREATE INDEX IF NOT EXISTS ix_fact_positions_case_party
  ON fact_positions(case_id, party_id);
CREATE INDEX IF NOT EXISTS ix_fact_positions_fact
  ON fact_positions(fact_id);

CREATE TABLE IF NOT EXISTS fact_revisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  fact_id uuid NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
  version int NOT NULL,
  proposition text NOT NULL,
  proof_status text NOT NULL,
  proof_basis text NOT NULL DEFAULT 'NONE',
  proven_by_decision_id uuid REFERENCES decisions(id) ON DELETE SET NULL,
  positions jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence_ids uuid[] NOT NULL DEFAULT '{}',
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  changed_by uuid REFERENCES users(id),
  reason text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (fact_id, version)
);
CREATE INDEX IF NOT EXISTS ix_fact_revisions_fact_version
  ON fact_revisions(fact_id, version);

ALTER TABLE evidence_links ADD COLUMN IF NOT EXISTS citation_id uuid REFERENCES citations(id) ON DELETE SET NULL;
ALTER TABLE evidence_links ADD COLUMN IF NOT EXISTS added_by uuid REFERENCES users(id);
ALTER TABLE evidence_links ADD COLUMN IF NOT EXISTS added_at timestamptz NOT NULL DEFAULT now();

-- Backfill de estados de prueba desde el estado epistemico heredado.
UPDATE facts
SET proof_status = CASE status
      WHEN 'JUDICIALLY_DETERMINED' THEN 'JUDICIALLY_PROVEN'
      WHEN 'SUPPORTED' THEN 'EVIDENCE_SUPPORTED'
      WHEN 'CONTRADICTED' THEN 'DISPUTED'
      WHEN 'DISPUTED' THEN 'DISPUTED'
      WHEN 'UNRESOLVED' THEN 'UNRESOLVED'
      ELSE 'ALLEGED'
    END,
    proof_basis = CASE status
      WHEN 'JUDICIALLY_DETERMINED' THEN 'JUDICIAL_DECISION'
      WHEN 'SUPPORTED' THEN 'EVIDENCE'
      ELSE 'NONE'
    END,
    proven_by_decision_id = CASE
      WHEN status = 'JUDICIALLY_DETERMINED' THEN determined_by_decision_id
      ELSE proven_by_decision_id
    END,
    proven_at = CASE
      WHEN status = 'JUDICIALLY_DETERMINED' THEN coalesce(proven_at, updated_at)
      ELSE proven_at
    END
WHERE proof_status = 'ALLEGED';

-- Posiciones de parte desde fact_claims + claims. La admision explicita se
-- agrega despues por revision humana o por el extractor; aqui no se inventa.
INSERT INTO fact_positions
  (organization_id, case_id, fact_id, party_id, side, position,
   source_claim_id, source_citation_id, confidence)
SELECT f.organization_id, f.case_id, fc.fact_id, cl.claimant_party_id,
       CASE
         WHEN p.role IN ('claimant','plaintiff','ejecutante') THEN 'DEMANDANTE'
         WHEN p.role IN ('defendant','respondent','ejecutado') THEN 'DEMANDADO'
         WHEN cl.claimant_party_id IS NULL THEN 'NO_ATRIBUIDO'
         ELSE 'TERCERO'
       END,
       CASE fc.stance WHEN 'asserts' THEN 'ALLEGES' ELSE 'DENIES' END,
       cl.id,
       (SELECT c.id FROM citations c
         WHERE c.target_type = 'claim' AND c.target_id = cl.id
         ORDER BY c.created_at LIMIT 1),
       cl.confidence
FROM fact_claims fc
JOIN facts f ON f.id = fc.fact_id
JOIN claims cl ON cl.id = fc.claim_id AND cl.case_id = f.case_id
LEFT JOIN parties p ON p.id = cl.claimant_party_id AND p.case_id = f.case_id
WHERE NOT EXISTS (
  SELECT 1 FROM fact_positions fp
  WHERE fp.fact_id = fc.fact_id
    AND fp.party_id IS NOT DISTINCT FROM cl.claimant_party_id
    AND fp.position = CASE fc.stance WHEN 'asserts' THEN 'ALLEGES' ELSE 'DENIES' END
);

-- Revision inicial para cada hecho (snapshot auditable).
INSERT INTO fact_revisions
  (organization_id, case_id, fact_id, version, proposition, proof_status,
   proof_basis, proven_by_decision_id, positions, evidence_ids, citations, reason)
SELECT f.organization_id, f.case_id, f.id, 1, f.proposition, f.proof_status,
       f.proof_basis, f.proven_by_decision_id,
       coalesce((
         SELECT jsonb_agg(jsonb_build_object(
           'party_id', fp.party_id, 'side', fp.side, 'position', fp.position,
           'source_claim_id', fp.source_claim_id,
           'source_citation_id', fp.source_citation_id))
         FROM fact_positions fp WHERE fp.fact_id = f.id
       ), '[]'::jsonb),
       coalesce((
         SELECT array_agg(el.evidence_id) FROM evidence_links el
         WHERE el.fact_id = f.id
       ), '{}'::uuid[]),
       coalesce((
         SELECT jsonb_agg(jsonb_build_object(
           'citation_id', c.id, 'source_type', c.source_type,
           'document_id', c.document_id, 'page_number', c.page_number,
           'media_id', c.media_id, 'segment_id', c.segment_id))
         FROM citations c WHERE c.target_type = 'fact' AND c.target_id = f.id
       ), '[]'::jsonb),
       'Backfill 0057: estado inicial'
FROM facts f
WHERE NOT EXISTS (SELECT 1 FROM fact_revisions fr WHERE fr.fact_id = f.id AND fr.version = 1);
