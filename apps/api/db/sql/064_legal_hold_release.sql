-- 064 - Respuesta retenida y liberación humana.
--
-- Cuando `require_second_opinion=true`, el chat no publica la respuesta
-- original. Se conserva aquí para que un revisor pueda liberarla después de
-- una segunda opinión COMPLETED. El contenido público (content/citations/
-- occurrences) permanece vacío/genérico mientras publication_blocked=true.

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS held_answer text;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS held_citations jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS held_occurrences jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS publication_blocked boolean NOT NULL DEFAULT false;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS publication_block_reason text;

CREATE INDEX IF NOT EXISTS ix_chat_messages_publication_blocked
  ON chat_messages(organization_id, session_id)
  WHERE publication_blocked;

COMMENT ON COLUMN chat_messages.held_answer IS
  'Respuesta original retenida por la política de segunda opinión; no se publica hasta release.';
COMMENT ON COLUMN chat_messages.held_citations IS
  'Citas originales de la respuesta retenida, en el mismo formato que citations.';
COMMENT ON COLUMN chat_messages.held_occurrences IS
  'Ubicaciones originales de la respuesta retenida.';
COMMENT ON COLUMN chat_messages.publication_blocked IS
  'true mientras la respuesta esté retenida por require_second_opinion.';
