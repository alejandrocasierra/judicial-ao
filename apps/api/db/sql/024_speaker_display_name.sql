-- Nombre visible del hablante (p. ej. "Alba Lucy Cock Alvarez" leído del video de
-- Teams donde el hablante activo aparece resaltado). Vacío/NULL => sin identificar.

ALTER TABLE speakers ADD COLUMN IF NOT EXISTS display_name text;

COMMENT ON COLUMN speakers.display_name IS
  'Nombre real del hablante (Teams: nombre resaltado del hablante activo). NULL = sin identificar.';
