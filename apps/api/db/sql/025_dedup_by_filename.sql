-- Deduplicación por (contenido + nombre): un expediente judicial puede tener el
-- MISMO documento archivado bajo nombres distintos (p. ej. "0010 Escrito..." y
-- "0037 SolicitaOficios..." son el mismo PDF). Se elimina la unicidad por sha256
-- y el control pasa a (case_id, sha256, filename) a nivel de aplicación.

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_case_id_sha256_key;
ALTER TABLE media DROP CONSTRAINT IF EXISTS media_case_id_sha256_key;
