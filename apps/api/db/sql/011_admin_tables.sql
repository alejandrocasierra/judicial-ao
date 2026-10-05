-- Tablas de administración: SMTP, agentes, skills, modelos IA, backups

CREATE TABLE smtp_settings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE UNIQUE,
  from_name text NOT NULL DEFAULT '',
  from_email text NOT NULL DEFAULT '',
  server text NOT NULL DEFAULT '',
  port int NOT NULL DEFAULT 587,
  security text NOT NULL DEFAULT 'STARTTLS' CHECK (security IN ('STARTTLS','SSL/TLS','NONE')),
  username text NOT NULL DEFAULT '',
  password_encrypted text,
  cc_emails text NOT NULL DEFAULT '',
  updated_by uuid REFERENCES users(id),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  system_prompt text NOT NULL DEFAULT '',
  skills text[] NOT NULL DEFAULT '{}',
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE skills (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  system_prompt text NOT NULL DEFAULT '',
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ai_models (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  provider text NOT NULL CHECK (provider IN ('anthropic','openai','gemini','kimi')),
  model_name text NOT NULL,
  api_key_ref text NOT NULL,
  is_default boolean NOT NULL DEFAULT false,
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE backups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  backup_type text NOT NULL CHECK (backup_type IN ('manual','scheduled')),
  status text NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED')),
  size_bytes bigint,
  storage_path text,
  created_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

CREATE INDEX idx_smtp_settings_org ON smtp_settings(organization_id);
CREATE INDEX idx_agents_org ON agents(organization_id);
CREATE INDEX idx_skills_org ON skills(organization_id);
CREATE INDEX idx_ai_models_org ON ai_models(organization_id);
CREATE INDEX idx_backups_org ON backups(organization_id, created_at DESC);

-- RLS para las tablas de administración (se crean después de 0001_initial)
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['smtp_settings','agents','skills','ai_models','backups'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = current_org()) WITH CHECK (organization_id = current_org())', t);
  END LOOP;
END $$;

-- Grants para el rol de aplicación (las tablas no existían cuando corrió 050_rls_grants.sql)
GRANT SELECT, INSERT, UPDATE ON smtp_settings, agents, skills, ai_models, backups TO "{{DB_APP_USER}}";
