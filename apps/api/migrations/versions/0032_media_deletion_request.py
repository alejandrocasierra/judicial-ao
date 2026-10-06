"""0032 — solicitud de eliminación de media (videos/audios), igual que documents.

Agrega a `media` las columnas deletion_requested_at / _by / _reason para poder
registrar la solicitud de eliminación desde el módulo Procesos. La evidencia no se
borra directamente: se marca y respeta la medida de conservación (legal hold).
"""
from alembic import op

revision = "0032_media_deletion_request"
down_revision = "0031_event_relationships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE media ADD COLUMN IF NOT EXISTS deletion_requested_at timestamptz")
    op.execute("ALTER TABLE media ADD COLUMN IF NOT EXISTS deletion_requested_by uuid REFERENCES users(id)")
    op.execute("ALTER TABLE media ADD COLUMN IF NOT EXISTS deletion_reason text")


def downgrade() -> None:
    op.execute("ALTER TABLE media DROP COLUMN IF EXISTS deletion_reason")
    op.execute("ALTER TABLE media DROP COLUMN IF EXISTS deletion_requested_by")
    op.execute("ALTER TABLE media DROP COLUMN IF EXISTS deletion_requested_at")
