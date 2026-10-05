-- Modo de OCR por documento: basico (local) o document_ai (Google Cloud).
-- NULL = no procesado aún; se usa para saber si el usuario eligió no hacer OCR.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_mode text CHECK (ocr_mode IN ('basico', 'document_ai'));
ALTER TABLE media ADD COLUMN IF NOT EXISTS asr_mode text CHECK (asr_mode IN ('basico', 'document_ai'));

-- Índice para buscar documentos pendientes de OCR por modo.
CREATE INDEX IF NOT EXISTS ix_documents_ocr_mode ON documents(ocr_mode) WHERE ocr_mode IS NOT NULL;

COMMENT ON COLUMN documents.ocr_mode IS 'Modo de OCR elegido: basico (Tesseract/Docling local) o document_ai (Google Document AI)';
COMMENT ON COLUMN media.asr_mode IS 'Modo de ASR elegido: basico (Whisper local) o document_ai (Google)';
