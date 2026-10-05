-- Progreso del OCR en vivo. Tabla aparte porque la transacción del pipeline
-- mantiene bloqueada la fila de `documents` durante todo el procesamiento; el
-- worker escribe aquí desde una conexión/transacción independiente para que el
-- monitor muestre el avance sin esperar al commit final.

CREATE TABLE IF NOT EXISTS document_ocr_progress (
  document_id uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  pct int NOT NULL DEFAULT 0 CHECK (pct BETWEEN 0 AND 100),
  detail text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE document_ocr_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_ocr_progress FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
                 AND tablename='document_ocr_progress' AND policyname='tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON document_ocr_progress
      USING (organization_id = current_org())
      WITH CHECK (organization_id = current_org());
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON document_ocr_progress TO "{{DB_APP_USER}}";
