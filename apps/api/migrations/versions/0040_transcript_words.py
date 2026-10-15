"""Timestamps por PALABRA en los segmentos de transcripción.

Los timestamps por palabra (que Whisper ya produce, pero se descartaban) permiten:
1. PARTIR un segmento ASR con precisión de milisegundo cuando cruza un cambio de
   hablante detectado por la diarización (antes: corte proporcional por caracteres).
2. Fusionar micropausas conservando el detalle por palabra (citas exactas).

Formato: [{word, start_ms, end_ms, confidence}, ...] (JSONB, nullable).
"""
from __future__ import annotations

from alembic import op


revision = "0040_transcript_words"
down_revision = "0039_media_scoped_speakers"
branch_labels = None
depends_on = None


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
ALTER TABLE transcript_segments ADD COLUMN IF NOT EXISTS words jsonb;
"""

DOWN = """
ALTER TABLE transcript_segments DROP COLUMN IF EXISTS words;
"""


def upgrade() -> None:
    _raw(UP)


def downgrade() -> None:
    _raw(DOWN)
