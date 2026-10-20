-- 048 — Historial de chat completo: incertidumbres y metadatos de ocurrencias.
-- Sin esto, al recargar una conversación se perdían las advertencias y la nota
-- "Menciones del término X" aunque el turno original sí las mostraba.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS uncertainties jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS occurrences_term text;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS occurrences_note text;

COMMENT ON COLUMN chat_messages.uncertainties IS
  'Lista de incertidumbres del turno (sin duplicar el mensaje de insuficiencia)';
COMMENT ON COLUMN chat_messages.occurrences_term IS
  'Término ubicado por locate() cuando la intención es navegar';
COMMENT ON COLUMN chat_messages.occurrences_note IS
  'Etiqueta de las menciones ("Menciones del término X. No son la respuesta.")'
