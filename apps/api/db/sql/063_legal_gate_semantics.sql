-- 062 - Gate semantico, precios de modelo y evidencia de EVIDENCE_SUPPORTED.
-- Aditivo. Las columnas de precio quedan NULL: no se inventan tarifas.
-- El costo de segunda opinion se marca ESTIMATED mientras no haya precio real.

ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS input_price_per_mtok numeric(12,6);
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS output_price_per_mtok numeric(12,6);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ai_models_price_nonnegative'
  ) THEN
    ALTER TABLE ai_models
      ADD CONSTRAINT ai_models_price_nonnegative CHECK (
        (input_price_per_mtok IS NULL OR input_price_per_mtok >= 0)
        AND (output_price_per_mtok IS NULL OR output_price_per_mtok >= 0)
      );
  END IF;
END $$;

ALTER TABLE second_opinion_runs
  ADD COLUMN IF NOT EXISTS cost_source text NOT NULL DEFAULT 'ESTIMATED';
ALTER TABLE second_opinion_runs
  ADD COLUMN IF NOT EXISTS comparison_mode text NOT NULL DEFAULT 'DETERMINISTIC';
ALTER TABLE second_opinion_runs
  ADD COLUMN IF NOT EXISTS comparison_error text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'second_opinion_cost_source_check'
  ) THEN
    ALTER TABLE second_opinion_runs
      ADD CONSTRAINT second_opinion_cost_source_check
      CHECK (cost_source IN ('MODEL_PRICE','ESTIMATED'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'second_opinion_comparison_mode_check'
  ) THEN
    ALTER TABLE second_opinion_runs
      ADD CONSTRAINT second_opinion_comparison_mode_check
      CHECK (comparison_mode IN ('LLM','DETERMINISTIC'));
  END IF;
END $$;
