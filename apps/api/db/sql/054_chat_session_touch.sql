-- 054 — Tocar la sesión al insertar un mensaje.
--
-- `list_sessions` ordena por `chat_sessions.updated_at`, pero el INSERT de
-- mensajes no actualizaba la sesión: al recargar, la conversación más reciente
-- no era la primera y el widget restauraba otra. Un trigger mantiene el orden
-- correcto en todos los caminos (chat, agente, tools, confirmaciones).

CREATE OR REPLACE FUNCTION touch_chat_session() RETURNS trigger AS $$
BEGIN
  UPDATE chat_sessions SET updated_at = now() WHERE id = NEW.session_id;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_chat_messages_touch_session ON chat_messages;
CREATE TRIGGER trg_chat_messages_touch_session
  AFTER INSERT ON chat_messages
  FOR EACH ROW EXECUTE FUNCTION touch_chat_session();
