-- 047 — Pipeline de chat: formato, coherencia, latencia y skills ejecutables.
-- answer_kind/intent/degraded permiten que el historial reproduzca exactamente
-- lo que el compositor decidió (y que la UI oculte ubicaciones cuando no toca).
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS answer_kind text;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS intent text;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS degraded boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN chat_messages.answer_kind IS
  'direct | partial | insufficient | navigational | correction';
COMMENT ON COLUMN chat_messages.intent IS
  'navegar | dato | analisis | procedimental | agregacion | lectura | correccion';
COMMENT ON COLUMN chat_messages.degraded IS
  'true si el turno se resolvió con el presupuesto de latencia agotado';

-- Memoria resumida por sesión (Ola 2): resumen de los turnos antiguos para no
-- enviar todo el historial al modelo en conversaciones largas.
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS summary text;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS summary_upto timestamptz;
COMMENT ON COLUMN chat_sessions.summary IS
  'Resumen determinista/LLM de los turnos anteriores a summary_upto';

-- Skills ejecutables (Ola 2): tools permitidas, parámetros y orden estable.
ALTER TABLE skills ADD COLUMN IF NOT EXISTS tools text[] NOT NULL DEFAULT '{}';
ALTER TABLE skills ADD COLUMN IF NOT EXISTS parameters jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS position integer;
COMMENT ON COLUMN skills.tools IS
  'Allowlist de tools que la skill puede usar; vacío = sin restricción';
COMMENT ON COLUMN skills.parameters IS
  'Parámetros por defecto inyectados en el prompt/ejecución de la skill';
COMMENT ON COLUMN skills.position IS
  'Orden estable dentro del agente (NULL = orden de enlace)';
