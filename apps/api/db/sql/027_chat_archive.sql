-- Archivado de sesiones de chat (Fase 3): DELETE /chats/{id} archiva, no borra.
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS archived_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_chat_sessions_case_active ON chat_sessions(case_id, updated_at DESC) WHERE archived_at IS NULL;
