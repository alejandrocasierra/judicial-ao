"""Hablantes a nivel de MEDIA (antes: nivel de caso, por label).

Problema: `speakers` tenía UNIQUE(case_id, label), así que TODOS los videos del caso
compartían las mismas filas SPEAKER_00..N. La diarización de un video (p. ej. 0083)
sobrescribía los `display_name` que otro video (0065) había resuelto, porque pyannote
numera los clusters de forma independiente por video → el mismo `SPEAKER_04` es una
persona distinta en cada grabación. Resultado: nombres cruzados entre videos y la
misma lista de hablantes en todos.

Solución: `speakers.media_id` + UNIQUE(case_id, media_id, label). Se clonan los
hablantes existentes a una fila por (media, label) según los segmentos que los usan,
se re-apuntan los `transcript_segments` y se eliminan las filas de nivel de caso.
(El nombre clonado es el que hubiera quedado; se corrige al re-diarizar cada video.)

`media_id` queda NULLABLE para permitir hablantes "manuales" creados desde la UI
(sin media), que igual aparecen en el visor del media cuyos segmentos los usan.
"""
from __future__ import annotations

from alembic import op


revision = "0039_media_scoped_speakers"
down_revision = "0038_jobs_reap_include_media"
branch_labels = None
depends_on = None


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
ALTER TABLE speakers ADD COLUMN IF NOT EXISTS media_id uuid;

-- Quitar la unicidad de nivel de caso (case_id, label) ANTES de clonar.
ALTER TABLE speakers DROP CONSTRAINT IF EXISTS speakers_case_id_label_key;

-- Clonar una fila de speaker por cada (media, label) realmente usado por segmentos.
INSERT INTO speakers (id, organization_id, case_id, media_id, label, speaker_role,
                      resolved_party_id, resolution_status, resolution_source, confidence,
                      version, display_name)
SELECT gen_random_uuid(), s.organization_id, s.case_id, u.media_id, s.label, s.speaker_role,
       s.resolved_party_id, s.resolution_status, s.resolution_source, s.confidence,
       s.version, s.display_name
FROM (SELECT DISTINCT media_id, speaker_id FROM transcript_segments WHERE speaker_id IS NOT NULL) u
JOIN speakers s ON s.id = u.speaker_id;

-- Re-apuntar los segmentos a la fila del media correspondiente.
UPDATE transcript_segments ts
SET speaker_id = ns.id
FROM speakers old, speakers ns
WHERE ts.speaker_id = old.id
  AND ns.case_id = old.case_id
  AND ns.label = old.label
  AND ns.media_id = ts.media_id;

-- Eliminar las filas de nivel de caso (ya no las usa ningún segmento).
DELETE FROM speakers WHERE media_id IS NULL;

ALTER TABLE speakers ADD CONSTRAINT speakers_case_media_label_key UNIQUE (case_id, media_id, label);
ALTER TABLE speakers ADD CONSTRAINT speakers_media_id_fkey FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_speakers_media ON speakers (media_id);
"""

DOWN = """
ALTER TABLE speakers DROP CONSTRAINT IF EXISTS speakers_media_id_fkey;
ALTER TABLE speakers DROP CONSTRAINT IF EXISTS speakers_case_media_label_key;
DROP INDEX IF EXISTS ix_speakers_media;
ALTER TABLE speakers DROP COLUMN IF EXISTS media_id;
ALTER TABLE speakers ADD CONSTRAINT speakers_case_id_label_key UNIQUE (case_id, label);
"""


def upgrade() -> None:
    _raw(UP)


def downgrade() -> None:
    _raw(DOWN)
