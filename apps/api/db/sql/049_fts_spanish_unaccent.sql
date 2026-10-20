-- 049 — FTS jurídico: español + acentos normalizados.
--
-- `FTS_CONFIG=simple` no hace stemming ni ignora tildes: "apelación" no casaba
-- con "apelacion" y "pago" no casaba con "pagos". Añadimos una columna generada
-- nueva (sin tocar la histórica `tsv`) para que el despliegue sea compatible y
-- el índice pueda reconstruirse sin bloquear la lectura anterior.
CREATE OR REPLACE FUNCTION public.immutable_unaccent(text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT translate(
        lower(coalesce($1, '')),
        'áàäâãéèëêíìïîóòöôõúùüûñçÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇ',
        'aaaaaeeeeiiiiooooouuuuncAAAAAEEEEIIIIOOOOOUUUUNC'
    )
$$;

COMMENT ON FUNCTION public.immutable_unaccent(text) IS
  'Normaliza tildes y mayúsculas; IMMUTABLE para columnas generadas e índices GIN';

ALTER TABLE document_pages
  ADD COLUMN IF NOT EXISTS search_tsv tsvector
  GENERATED ALWAYS AS (
    to_tsvector('spanish', public.immutable_unaccent(coalesce(text, '')))
  ) STORED;

ALTER TABLE transcript_segments
  ADD COLUMN IF NOT EXISTS search_tsv tsvector
  GENERATED ALWAYS AS (
    to_tsvector('spanish', public.immutable_unaccent(coalesce(text, '')))
  ) STORED;

ALTER TABLE chunks
  ADD COLUMN IF NOT EXISTS search_tsv tsvector
  GENERATED ALWAYS AS (
    to_tsvector('spanish', public.immutable_unaccent(coalesce(text, '')))
  ) STORED;

CREATE INDEX IF NOT EXISTS ix_pages_search_tsv ON document_pages USING gin(search_tsv);
CREATE INDEX IF NOT EXISTS ix_segments_search_tsv ON transcript_segments USING gin(search_tsv);
CREATE INDEX IF NOT EXISTS ix_chunks_search_tsv ON chunks USING gin(search_tsv);

COMMENT ON COLUMN document_pages.search_tsv IS 'FTS español sin tildes (0049)';
COMMENT ON COLUMN transcript_segments.search_tsv IS 'FTS español sin tildes (0049)';
COMMENT ON COLUMN chunks.search_tsv IS 'FTS español sin tildes (0049)';
