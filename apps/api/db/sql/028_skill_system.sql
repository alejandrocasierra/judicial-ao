-- Fase 5: skills de sistema (sembradas automáticamente) visibles con badge en el panel.
ALTER TABLE skills ADD COLUMN IF NOT EXISTS is_system boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS ix_skills_org_system ON skills(organization_id, is_system);
