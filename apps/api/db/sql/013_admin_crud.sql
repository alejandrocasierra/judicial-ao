-- Fase 8 ampliada: CRUD de agentes/skills/modelos/roles + diccionario OCR + edición de páginas/segmentos.

-- 1. Proveedores de modelos: añadir DeepSeek y personalizado.
ALTER TABLE ai_models DROP CONSTRAINT IF EXISTS ai_models_provider_check;
ALTER TABLE ai_models ADD CONSTRAINT ai_models_provider_check
  CHECK (provider IN ('anthropic','openai','gemini','kimi','deepseek','custom'));
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS encrypted_api_key text;
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

-- 2. Roles editables por organización (los de sistema se siembran desde RBAC).
CREATE TABLE IF NOT EXISTS roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  code text NOT NULL,
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  permissions text[] NOT NULL DEFAULT '{}',
  is_system boolean NOT NULL DEFAULT false,
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, code)
);

-- 3. Diccionario OCR (mejora continua del reconocimiento por organización).
CREATE TABLE IF NOT EXISTS ocr_terms (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  term text NOT NULL,                -- término tal como lo escribió el humano
  normalized text NOT NULL,          -- forma normalizada (minúsculas, sin acentos)
  occurrences int NOT NULL DEFAULT 1,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, normalized)
);

-- 4. Permitir roles personalizados en users.org_role (la validación se hace en la app).
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_org_role_check;

-- 5. RLS de las tablas nuevas.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['roles','ocr_terms'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) WITH CHECK (organization_id = current_org())', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON roles, ocr_terms TO "{{DB_APP_USER}}";
GRANT DELETE ON agents, skills, ai_models TO "{{DB_APP_USER}}";
