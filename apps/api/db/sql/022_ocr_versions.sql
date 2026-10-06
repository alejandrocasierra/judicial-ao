-- Versiones de OCR por página: permite conservar el resultado de cada motor
-- (basico / document_ai) para compararlos en el visor sin reprocesar.

CREATE TABLE IF NOT EXISTS document_ocr_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_number int NOT NULL CHECK (page_number >= 1),
  mode text NOT NULL CHECK (mode IN ('basico', 'document_ai')),
  text text NOT NULL DEFAULT '',
  ocr_confidence numeric(6,5) CHECK (ocr_confidence BETWEEN 0 AND 1),
  layout_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, page_number, mode)
);

CREATE INDEX IF NOT EXISTS ix_ocr_versions_document ON document_ocr_versions(document_id, mode);

-- RLS tenant (mismo patrón que el resto de tablas).
ALTER TABLE document_ocr_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_ocr_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON document_ocr_versions
  USING (organization_id = current_org())
  WITH CHECK (organization_id = current_org());

GRANT SELECT, INSERT, UPDATE, DELETE ON document_ocr_versions TO "{{DB_APP_USER}}";
