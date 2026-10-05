-- 019 — Corrección humana del OCR de una página.
--
-- Marca las páginas cuyo texto fue revisado/ajustado por una persona, con su autor y
-- fecha, para que un reproceso automático (job document_ocr) no las sobrescriba.
ALTER TABLE document_pages ADD COLUMN IF NOT EXISTS human_corrected boolean NOT NULL DEFAULT false;
ALTER TABLE document_pages ADD COLUMN IF NOT EXISTS corrected_at timestamptz;
ALTER TABLE document_pages ADD COLUMN IF NOT EXISTS corrected_by uuid REFERENCES users(id);

CREATE INDEX IF NOT EXISTS ix_document_pages_human_corrected
  ON document_pages (document_id) WHERE human_corrected;
