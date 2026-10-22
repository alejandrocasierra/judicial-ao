-- 062 - Invariantes de estado de prueba en facts.
-- Reconstruida a partir de la revisión aplicada en la base local
-- (`0062_legal_p1_invariants`): facts.proof_status no puede declarar
-- EVIDENCE_SUPPORTED sin evidencia que lo sostenga, ADMITTED sin admisión ni
-- JUDICIALLY_PROVEN sin decisión con documento fuente.

CREATE OR REPLACE FUNCTION fact_proof_invariant()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  dec_id uuid;
  has_source boolean := false;
BEGIN
  IF NEW.proof_status = 'JUDICIALLY_PROVEN' THEN
    dec_id := coalesce(NEW.proven_by_decision_id, NEW.determined_by_decision_id);
    IF dec_id IS NULL THEN
      RAISE EXCEPTION 'JUDICIALLY_PROVEN_REQUIRES_DECISION' USING ERRCODE = '23514';
    END IF;
    SELECT (d.source_document_id IS NOT NULL) INTO has_source
    FROM decisions d
    WHERE d.id = dec_id AND d.case_id = NEW.case_id
      AND d.organization_id = NEW.organization_id;
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
  ELSIF NEW.proof_status = 'EVIDENCE_SUPPORTED' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM evidence_links el
      JOIN evidence e ON e.id = el.evidence_id
      WHERE el.fact_id = NEW.id
        AND el.organization_id = NEW.organization_id
        AND el.stance = 'supports'
        AND e.case_id = NEW.case_id
        AND coalesce(e.admissibility_status, 'unknown') <> 'rejected'
    ) THEN
      RAISE EXCEPTION 'EVIDENCE_SUPPORTED_REQUIRES_EVIDENCE' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $function$;

DROP TRIGGER IF EXISTS trg_facts_proof_invariant ON facts;
CREATE CONSTRAINT TRIGGER trg_facts_proof_invariant
  AFTER INSERT OR UPDATE OF proof_status, proven_by_decision_id, determined_by_decision_id
  ON facts
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION fact_proof_invariant();
