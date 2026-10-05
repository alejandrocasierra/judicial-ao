-- Flags de capacidad por modelo: OCR y ASR (máximo uno activo de cada tipo por org).

ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS ocr_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS asr_enabled boolean NOT NULL DEFAULT false;

-- Índices únicos parciales: como mucho un modelo con cada capacidad por organización.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_models_ocr_enabled
  ON ai_models(organization_id) WHERE ocr_enabled;
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_models_asr_enabled
  ON ai_models(organization_id) WHERE asr_enabled;
