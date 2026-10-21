-- 053 — Autor del mensaje de usuario en el chat.
--
-- Las sesiones son compartidas por el expediente: `chat_sessions.created_by` es
-- quien la creó, no necesariamente quien preguntó. El aprendizaje del perfil
-- atribuía la reformulación exitosa al creador de la sesión, así que la forma de
-- preguntar de un colega podía entrenar el perfil de otro. Esta columna guarda
-- el autor real de cada mensaje de usuario (NULL en el histórico, donde se cae
-- al creador de la sesión).

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS user_id uuid
  REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_chat_messages_user
  ON chat_messages(user_id) WHERE user_id IS NOT NULL;

COMMENT ON COLUMN chat_messages.user_id IS
  'Autor del mensaje (solo role=user); atribuye el aprendizaje al usuario correcto';
