-- Chat IA: preferencia de motor OCR/ASR por sesión.
-- NULL = usar todos los motores disponibles (comportamiento histórico).
-- 'basico' = solo motores locales (Tesseract/Whisper).
-- 'document_ai' = solo Google Document AI / ASR de Document AI.

ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS ocr_mode text CHECK (ocr_mode IN ('basico', 'document_ai', 'todos'));
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS asr_mode text CHECK (asr_mode IN ('basico', 'document_ai', 'todos'));

COMMENT ON COLUMN chat_sessions.ocr_mode IS 'Modo OCR preferido para esta conversación: basico, document_ai o todos';
COMMENT ON COLUMN chat_sessions.asr_mode IS 'Modo ASR preferido para esta conversación: basico, document_ai o todos';
