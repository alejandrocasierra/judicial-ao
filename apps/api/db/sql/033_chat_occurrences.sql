-- 033 — Persiste las "apariciones" (occurrences) del chat.
-- Las citas ya se guardaban en chat_messages.citations; este bloque ("Aparece en N
-- ubicaciones") solo vivía en memoria y se perdía al cambiar de conversación o al
-- enviar otro mensaje. Se guarda por mensaje para que el historial lo reproduzca.

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS occurrences jsonb NOT NULL DEFAULT '[]'::jsonb;
