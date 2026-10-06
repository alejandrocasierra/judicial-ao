"""0034 — columna `version` en `users` (bloqueo optimista al editar).

`PATCH /admin/users/{id}` y `GET /admin/users/{id}` usan `expected_version`/
`version`, pero la tabla nunca tuvo esa columna: editar un usuario fallaba.
"""
from alembic import op

revision = "0034_user_version"
down_revision = "0033_ocr_confidence_precision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS version")
