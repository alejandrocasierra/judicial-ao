-- Purga controlada de expedientes (módulo Procesos).
--
-- La eliminación total de un proceso es una operación excepcional: la API la
-- ejecuta en una única transacción con SET LOCAL app.allow_evidence_purge='on'.
-- Los triggers siguen bloqueando cualquier DELETE fuera de ese contexto.

-- 1) protect_case_delete: permite borrar el caso sólo con la bandera de purga.
CREATE OR REPLACE FUNCTION protect_case_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.legal_hold THEN RAISE EXCEPTION 'LEGAL_HOLD_ACTIVE' USING ERRCODE = '42501'; END IF;
  IF coalesce(current_setting('app.allow_evidence_purge', true), '') <> 'on' THEN
    RAISE EXCEPTION 'CASE_DELETE_BLOCKED' USING ERRCODE = '42501';
  END IF;
  RETURN OLD;
END $$;

-- 2) audit_append_only: con la bandera de purga se permite DELETE (nunca UPDATE).
CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND coalesce(current_setting('app.allow_evidence_purge', true), '') = 'on' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'AUDIT_APPEND_ONLY' USING ERRCODE = '42501';
END $$;

-- 3) Grants DELETE para las tablas del expediente (los triggers siguen siendo el guardián).
GRANT DELETE ON cases, parties, documents, document_pages, media, speakers, transcript_segments,
  entities, decisions, events, claims, facts, fact_claims, evidence, evidence_links,
  citations, contradictions, issues, jobs, model_runs, reviews, audit_logs TO "{{DB_APP_USER}}";

-- 4) audit_logs no tiene política DELETE (append-only también vía RLS): la purga es la
-- única vía, exigiendo además la bandera en la propia política.
CREATE POLICY audit_purge_delete ON audit_logs FOR DELETE
  USING (organization_id = current_org()
         AND coalesce(current_setting('app.allow_evidence_purge', true), '') = 'on');
