"""Linaje procesal: cuaderno, índice y orden para documents/media.

Fase 1 del plan: el XLSX de índice es la fuente de verdad procesal. Cada archivo
hereda cuaderno, número de índice, nombre original del índice, sub-orden dentro
del ítem y un orden procesal global consultable. El índice maestro general se
marca como documento especial del caso.
"""
from __future__ import annotations

from alembic import op

revision = "0004_cuaderno_linaje"
down_revision = "0003_audit_chain_id_order"
branch_labels = None
depends_on = None


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
ALTER TABLE documents
    ADD COLUMN cuaderno text,
    ADD COLUMN indice_numero int,
    ADD COLUMN indice_nombre_original text,
    ADD COLUMN sub_orden int NOT NULL DEFAULT 0,
    ADD COLUMN orden_procesal text,
    ADD COLUMN es_indice_maestro boolean NOT NULL DEFAULT false;

ALTER TABLE media
    ADD COLUMN cuaderno text,
    ADD COLUMN indice_numero int,
    ADD COLUMN indice_nombre_original text,
    ADD COLUMN sub_orden int NOT NULL DEFAULT 0,
    ADD COLUMN orden_procesal text,
    ADD COLUMN es_indice_maestro boolean NOT NULL DEFAULT false;

-- Ordenamiento dentro de un cuaderno.
CREATE INDEX ix_documents_cuaderno_orden ON documents(case_id, cuaderno, orden_procesal NULLS LAST);
CREATE INDEX ix_media_cuaderno_orden ON media(case_id, cuaderno, orden_procesal NULLS LAST);

-- Ordenamiento global por caso (el que usa /documents).
CREATE INDEX ix_documents_case_orden ON documents(case_id, orden_procesal NULLS LAST);
CREATE INDEX ix_media_case_orden ON media(case_id, orden_procesal NULLS LAST);

-- Evita duplicados lógicos del mismo ítem de índice dentro de un cuaderno.
-- Los maestros generales tienen indice_numero IS NULL y quedan fuera.
CREATE UNIQUE INDEX ix_documents_cuaderno_indice_sub
    ON documents(case_id, cuaderno, indice_numero, sub_orden)
    WHERE indice_numero IS NOT NULL;
CREATE UNIQUE INDEX ix_media_cuaderno_indice_sub
    ON media(case_id, cuaderno, indice_numero, sub_orden)
    WHERE indice_numero IS NOT NULL;

-- Integridad básica de los valores de linaje.
ALTER TABLE documents ADD CONSTRAINT ck_documents_sub_orden_nonnegative CHECK (sub_orden >= 0);
ALTER TABLE documents ADD CONSTRAINT ck_documents_indice_numero_positive CHECK (indice_numero IS NULL OR indice_numero > 0);
ALTER TABLE media ADD CONSTRAINT ck_media_sub_orden_nonnegative CHECK (sub_orden >= 0);
ALTER TABLE media ADD CONSTRAINT ck_media_indice_numero_positive CHECK (indice_numero IS NULL OR indice_numero > 0);
"""

DOWN = """
ALTER TABLE media DROP CONSTRAINT IF EXISTS ck_media_indice_numero_positive;
ALTER TABLE media DROP CONSTRAINT IF EXISTS ck_media_sub_orden_nonnegative;
ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_indice_numero_positive;
ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_sub_orden_nonnegative;

DROP INDEX IF EXISTS ix_media_cuaderno_indice_sub;
DROP INDEX IF EXISTS ix_documents_cuaderno_indice_sub;
DROP INDEX IF EXISTS ix_media_case_orden;
DROP INDEX IF EXISTS ix_documents_case_orden;
DROP INDEX IF EXISTS ix_media_cuaderno_orden;
DROP INDEX IF EXISTS ix_documents_cuaderno_orden;

ALTER TABLE documents
    DROP COLUMN IF EXISTS cuaderno,
    DROP COLUMN IF EXISTS indice_numero,
    DROP COLUMN IF EXISTS indice_nombre_original,
    DROP COLUMN IF EXISTS sub_orden,
    DROP COLUMN IF EXISTS orden_procesal,
    DROP COLUMN IF EXISTS es_indice_maestro;

ALTER TABLE media
    DROP COLUMN IF EXISTS cuaderno,
    DROP COLUMN IF EXISTS indice_numero,
    DROP COLUMN IF EXISTS indice_nombre_original,
    DROP COLUMN IF EXISTS sub_orden,
    DROP COLUMN IF EXISTS orden_procesal,
    DROP COLUMN IF EXISTS es_indice_maestro;
"""


def upgrade() -> None:
    _raw(UP)


def downgrade() -> None:
    _raw(DOWN)
