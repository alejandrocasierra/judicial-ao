-- Módulo Procesos: árbol de carpetas por expediente + archivos genéricos.
--
-- case_folders: carpetas anidadas (n niveles) dentro de un expediente.
-- case_files:   archivos que NO pasan por el pipeline OCR/ASR
--               (xlsx, docx, imágenes, svg...). Los PDF y videos siguen
--               viviendo en documents/media y se enlazan a la carpeta
--               con la columna folder_id.

CREATE TABLE IF NOT EXISTS case_folders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  parent_id uuid REFERENCES case_folders(id) ON DELETE CASCADE,
  name text NOT NULL,
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Una carpeta no puede repetir nombre dentro del mismo padre (la raíz es parent_id NULL).
CREATE UNIQUE INDEX IF NOT EXISTS ux_case_folders_sibling_name
  ON case_folders(case_id, coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name));

CREATE INDEX IF NOT EXISTS ix_case_folders_case_parent ON case_folders(case_id, parent_id);

CREATE TABLE IF NOT EXISTS case_files (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  case_id uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  folder_id uuid REFERENCES case_folders(id) ON DELETE SET NULL,
  storage_uri text NOT NULL,
  sha256 char(64) NOT NULL,
  size_bytes bigint NOT NULL,
  mime_type text NOT NULL,
  filename text NOT NULL,
  uploaded_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, sha256)
);

CREATE INDEX IF NOT EXISTS ix_case_files_case_folder ON case_files(case_id, folder_id);

-- Enlace de la evidencia existente (PDFs / videos) a su carpeta del proceso.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS folder_id uuid REFERENCES case_folders(id) ON DELETE SET NULL;
ALTER TABLE media ADD COLUMN IF NOT EXISTS folder_id uuid REFERENCES case_folders(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_documents_case_folder ON documents(case_id, folder_id);
CREATE INDEX IF NOT EXISTS ix_media_case_folder ON media(case_id, folder_id);

-- RLS tenant (mismo patrón que el resto de tablas).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['case_folders','case_files'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = t AND policyname = 'tenant_isolation') THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) WITH CHECK (organization_id = current_org())', t);
    END IF;
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON case_folders, case_files TO "{{DB_APP_USER}}";
