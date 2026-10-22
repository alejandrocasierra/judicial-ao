-- 058 - Cierre de hechos: RLS, grants, indices, triggers e indice de chunks.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'legal_guard_policies','case_verification_settings','answer_audits',
    'answer_audit_claims','answer_audit_citations','answer_audit_findings',
    'legal_rule_citations','second_opinion_runs','second_opinion_claims',
    'second_opinion_findings','fact_positions','fact_revisions'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename=t AND policyname='tenant_isolation') THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) WITH CHECK (organization_id = current_org())', t);
    END IF;
  END LOOP;
END $$;

-- Politicas y configuracion: CRUD normal.
GRANT SELECT, INSERT, UPDATE, DELETE ON legal_guard_policies, case_verification_settings TO "{{DB_APP_USER}}";
-- Auditorias y segunda opinion: append-only.
GRANT SELECT, INSERT ON answer_audits, answer_audit_claims, answer_audit_citations,
  answer_audit_findings, second_opinion_runs, second_opinion_claims,
  second_opinion_findings, fact_revisions, legal_rule_citations TO "{{DB_APP_USER}}";
-- Posiciones: se corrigen con revision humana.
GRANT SELECT, INSERT, UPDATE, DELETE ON fact_positions TO "{{DB_APP_USER}}";
GRANT SELECT ON legal_rules TO "{{DB_APP_USER}}";

-- Triggers de misma organizacion para las FKs logicas nuevas.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
    ('legal_guard_policies','cases','case_id'),
    ('legal_guard_policies','ai_models','second_opinion_model_id'),
    ('case_verification_settings','cases','case_id'),
    ('case_verification_settings','legal_guard_policies','policy_id'),
    ('case_verification_settings','ai_models','second_opinion_model_id'),
    ('answer_audits','cases','case_id'),
    ('answer_audits','chat_messages','chat_message_id'),
    ('answer_audits','model_runs','model_run_id'),
    ('answer_audits','legal_guard_policies','policy_id'),
    ('answer_audit_claims','answer_audits','audit_id'),
    ('answer_audit_citations','answer_audits','audit_id'),
    ('answer_audit_citations','citations','citation_id'),
    ('answer_audit_findings','answer_audits','audit_id'),
    ('legal_rule_citations','cases','case_id'),
    ('legal_rule_citations','legal_rules','legal_rule_id'),
    ('second_opinion_runs','cases','case_id'),
    ('second_opinion_runs','answer_audits','audit_id'),
    ('second_opinion_runs','chat_messages','chat_message_id'),
    ('second_opinion_runs','model_runs','responder_model_run_id'),
    ('second_opinion_runs','ai_models','verifier_model_id'),
    ('second_opinion_runs','model_runs','model_run_id'),
    ('second_opinion_claims','second_opinion_runs','run_id'),
    ('second_opinion_findings','second_opinion_runs','run_id'),
    ('fact_positions','cases','case_id'),
    ('fact_positions','facts','fact_id'),
    ('fact_positions','parties','party_id'),
    ('fact_positions','claims','source_claim_id'),
    ('fact_positions','citations','source_citation_id'),
    ('fact_revisions','cases','case_id'),
    ('fact_revisions','facts','fact_id'),
    ('fact_revisions','decisions','proven_by_decision_id'),
    ('evidence_links','evidence','evidence_id'),
    ('evidence_links','facts','fact_id'),
    ('evidence_links','citations','citation_id')
  ) AS x(child, parent, col)
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = format('trg_same_org_%s_%s', r.child, r.col)) THEN
      EXECUTE format('CREATE TRIGGER trg_same_org_%s_%s BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION enforce_same_org(%L, %L)',
                     r.child, r.col, r.child, r.parent, r.col);
    END IF;
  END LOOP;
END $$;

-- Indices de consulta.
CREATE INDEX IF NOT EXISTS ix_facts_case_proof ON facts(case_id, proof_status);
CREATE INDEX IF NOT EXISTS ix_facts_case_proven ON facts(case_id, is_proven) WHERE is_proven;
CREATE INDEX IF NOT EXISTS ix_facts_case_issue ON facts(case_id, issue_id);
CREATE INDEX IF NOT EXISTS ix_chunks_metadata_gin ON chunks USING gin (metadata jsonb_path_ops);
CREATE INDEX IF NOT EXISTS ix_chunks_case_type ON chunks(case_id, chunk_type);
CREATE INDEX IF NOT EXISTS ix_graph_edges_case_type_target ON graph_edges(case_id, edge_type, target_node_id);

-- El hecho probado solo puede existir con decision del mismo expediente y
-- documento fuente, o con una admision registrada. La comprobacion es diferida
-- para permitir insertar hecho y posiciones en la misma transaccion.
CREATE OR REPLACE FUNCTION fact_proof_invariant() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE dec_id uuid; has_source boolean := false;
BEGIN
  IF NEW.proof_status = 'JUDICIALLY_PROVEN' THEN
    dec_id := coalesce(NEW.proven_by_decision_id, NEW.determined_by_decision_id);
    IF dec_id IS NULL THEN
      RAISE EXCEPTION 'JUDICIALLY_PROVEN_REQUIRES_DECISION' USING ERRCODE = '23514';
    END IF;
    SELECT (d.source_document_id IS NOT NULL) INTO has_source
    FROM decisions d WHERE d.id = dec_id AND d.case_id = NEW.case_id AND d.organization_id = NEW.organization_id;
    IF coalesce(has_source, false) IS NOT TRUE THEN
      RAISE EXCEPTION 'JUDICIALLY_PROVEN_REQUIRES_SOURCE_DOCUMENT' USING ERRCODE = '23514';
    END IF;
  ELSIF NEW.proof_status = 'ADMITTED' THEN
    IF NOT EXISTS (
      SELECT 1 FROM fact_positions fp
      WHERE fp.fact_id = NEW.id AND fp.organization_id = NEW.organization_id
        AND fp.position = 'ADMITS'
    ) THEN
      RAISE EXCEPTION 'ADMITTED_REQUIRES_PARTY_ADMISSION' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_facts_proof_invariant ON facts;
CREATE CONSTRAINT TRIGGER trg_facts_proof_invariant
  AFTER INSERT OR UPDATE OF proof_status, proven_by_decision_id, determined_by_decision_id ON facts
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION fact_proof_invariant();

-- Chunks: nuevo tipo `fact` para el ledger; no cambia la dimension del vector.
ALTER TABLE chunks DROP CONSTRAINT IF EXISTS chunks_chunk_type_check;
ALTER TABLE chunks ADD CONSTRAINT chunks_chunk_type_check
  CHECK (chunk_type IN ('document_section','paragraph','claim','fact','testimony_segment',
                        'timeline_event','legal_rule','evidence_description','decision_reasoning'));
