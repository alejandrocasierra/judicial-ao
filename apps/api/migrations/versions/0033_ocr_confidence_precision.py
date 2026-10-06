"""0033 — mayor precisión en la confianza OCR: numeric(4,3) → numeric(6,5).

El modelo de confianza ahora es por CARACTERES (distancia de Levenshtein): una
edición de una sola letra en una página larga baja muy poco, así que 3 decimales
no alcanzaban a representarlo (p. ej. 0.99975 se redondeaba a 1.000).
"""
from alembic import op

revision = "0033_ocr_confidence_precision"
down_revision = "0032_media_deletion_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_pages ALTER COLUMN ocr_confidence TYPE numeric(6,5)")
    op.execute("ALTER TABLE document_ocr_versions ALTER COLUMN ocr_confidence TYPE numeric(6,5)")


def downgrade() -> None:
    op.execute("ALTER TABLE document_pages ALTER COLUMN ocr_confidence TYPE numeric(4,3)")
    op.execute("ALTER TABLE document_ocr_versions ALTER COLUMN ocr_confidence TYPE numeric(4,3)")
