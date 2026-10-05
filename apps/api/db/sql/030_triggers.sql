-- =====================================================================
-- Reglas de integridad que NO dependen de la aplicación
-- =====================================================================

-- 1) Consistencia multi-tenant: la fila hija debe pertenecer a la misma organización que su padre
CREATE FUNCTION enforce_same_org() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE parent_table text := TG_ARGV[0]; fk_col text := TG_ARGV[1]; parent_id uuid; ok boolean;
BEGIN
  EXECUTE format('SELECT ($1).%I', fk_col) INTO parent_id USING NEW;
  IF parent_id IS NULL THEN RETURN NEW; END IF;
  EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE id = $1 AND organization_id = $2)', parent_table)
    INTO ok USING parent_id, NEW.organization_id;
  IF NOT ok THEN
    RAISE EXCEPTION 'TENANT_MISMATCH: %.% does not belong to organization', parent_table, fk_col USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;

DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
    ('case_members','cases','case_id'), ('case_members','users','user_id'),
    ('parties','cases','case_id'), ('documents','cases','case_id'), ('media','cases','case_id'),
    ('document_pages','documents','document_id'), ('transcript_segments','media','media_id'),
    ('transcript_segments','speakers','speaker_id'), ('speakers','cases','case_id'), ('speakers','parties','resolved_party_id'),
    ('entities','cases','case_id'), ('events','cases','case_id'), ('claims','cases','case_id'),
    ('claims','parties','claimant_party_id'), ('facts','cases','case_id'), ('facts','decisions','determined_by_decision_id'),
    ('fact_claims','facts','fact_id'), ('fact_claims','claims','claim_id'),
    ('evidence','cases','case_id'), ('evidence','documents','source_document_id'), ('evidence','media','source_media_id'),
    ('evidence_links','evidence','evidence_id'), ('evidence_links','facts','fact_id'),
    ('citations','cases','case_id'), ('citations','documents','document_id'), ('citations','media','media_id'),
    ('citations','transcript_segments','segment_id'),
    ('contradictions','cases','case_id'), ('contradictions','claims','claim_a_id'), ('contradictions','claims','claim_b_id'),
    ('decisions','cases','case_id'), ('decisions','documents','source_document_id'), ('issues','cases','case_id'),
    ('jobs','cases','case_id'), ('reviews','cases','case_id'), ('chunks','cases','case_id'), ('refresh_tokens','users','user_id')
  ) AS t(child, parent, col) LOOP
    EXECUTE format('CREATE TRIGGER trg_same_org_%s_%s BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION enforce_same_org(%L, %L)',
                   r.child, r.col, r.child, r.parent, r.col);
  END LOOP;
END $$;

-- 2) Inmutabilidad de originales (SSD §3.1, §127) y bloqueo por legal hold (§27)
CREATE FUNCTION protect_originals() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE hold boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT legal_hold INTO hold FROM cases WHERE id = OLD.case_id;
    IF hold THEN RAISE EXCEPTION 'LEGAL_HOLD_ACTIVE' USING ERRCODE = '42501'; END IF;
    IF coalesce(current_setting('app.allow_evidence_purge', true), '') <> 'on' THEN
      RAISE EXCEPTION 'DOCUMENT_IMMUTABLE' USING ERRCODE = '42501';
    END IF;
    RETURN OLD;
  END IF;
  IF NEW.sha256 IS DISTINCT FROM OLD.sha256 OR NEW.storage_uri IS DISTINCT FROM OLD.storage_uri
     OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
     OR NEW.filename IS DISTINCT FROM OLD.filename OR NEW.case_id IS DISTINCT FROM OLD.case_id
     OR NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by THEN
    RAISE EXCEPTION 'DOCUMENT_IMMUTABLE' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_documents_immutable BEFORE UPDATE OR DELETE ON documents FOR EACH ROW EXECUTE FUNCTION protect_originals();
CREATE TRIGGER trg_media_immutable BEFORE UPDATE OR DELETE ON media FOR EACH ROW EXECUTE FUNCTION protect_originals();

-- 3) Legal hold: no se permite borrar expedientes en hold ni sacarlos de hold sin rol admin (controlado por API)
CREATE FUNCTION protect_case_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.legal_hold THEN RAISE EXCEPTION 'LEGAL_HOLD_ACTIVE' USING ERRCODE = '42501'; END IF;
  RAISE EXCEPTION 'CASE_DELETE_BLOCKED' USING ERRCODE = '42501';
END $$;
CREATE TRIGGER trg_cases_no_delete BEFORE DELETE ON cases FOR EACH ROW EXECUTE FUNCTION protect_case_delete();

-- 4) Auditoría append-only con cadena de hash (evidencia de manipulación)
CREATE FUNCTION audit_chain() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE last_hash char(64);
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('audit_chain'));
  SELECT hash INTO last_hash FROM audit_logs ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := last_hash;
  NEW.hash := encode(digest(coalesce(last_hash,'') || NEW.id::text || coalesce(NEW.organization_id::text,'') ||
              coalesce(NEW.actor_id::text,'') || NEW.action || coalesce(NEW.entity_id,'') ||
              coalesce(NEW.before::text,'') || coalesce(NEW.after::text,'') || NEW.created_at::text, 'sha256'), 'hex');
  RETURN NEW;
END $$;
CREATE TRIGGER trg_audit_chain BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_chain();

CREATE FUNCTION audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'AUDIT_APPEND_ONLY' USING ERRCODE = '42501'; END $$;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_append_only();
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_logs FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only();

-- 5) Reviews (historial humano) también es append-only (SSD §34, §95)
CREATE TRIGGER trg_reviews_no_update BEFORE UPDATE OR DELETE ON reviews FOR EACH ROW EXECUTE FUNCTION audit_append_only();
-- Por sentencia: bloquea también UPDATE/DELETE que no afecten filas y TRUNCATE (los triggers por fila no se disparan)
CREATE TRIGGER trg_reviews_no_update_stmt BEFORE UPDATE OR DELETE OR TRUNCATE ON reviews FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only();
CREATE TRIGGER trg_audit_no_update_stmt BEFORE UPDATE OR DELETE ON audit_logs FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only();

-- 6) updated_at automático
CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;
CREATE TRIGGER trg_cases_touch BEFORE UPDATE ON cases FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_users_touch BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_claims_touch BEFORE UPDATE ON claims FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_facts_touch BEFORE UPDATE ON facts FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_jobs_touch BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 7) Un hecho sólo puede estar determinado por una decisión del MISMO expediente
CREATE FUNCTION fact_decision_same_case() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NEW.determined_by_decision_id IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM decisions WHERE id = NEW.determined_by_decision_id AND case_id = NEW.case_id) THEN
    RAISE EXCEPTION 'JUDICIAL_DETERMINATION_REQUIRES_DECISION' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_fact_decision BEFORE INSERT OR UPDATE ON facts FOR EACH ROW EXECUTE FUNCTION fact_decision_same_case();
