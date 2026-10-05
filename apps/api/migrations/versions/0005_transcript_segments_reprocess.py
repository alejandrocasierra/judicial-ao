"""Reprocesamiento de media: GRANT DELETE sobre transcript_segments.

Los transcript_segments son artefactos DERIVADOS (no originales): se regeneran
cada vez que se reprocesa un medio con un modelo ASR/diarización mejor. Sin
DELETE, el rol de aplicación no puede limpiar segmentos de una corrida anterior
y el reprocesamiento duplicaría filas. Los originales (media, documents) siguen
protegidos por protect_originals(); los segmentos no son evidencia primaria.

{{VAR}} se reemplaza con variables de entorno (misma lista blanca que 0001).
"""
import os
import re

from alembic import op

revision = "0005_segments_reprocess"
down_revision = "0004_cuaderno_linaje"
branch_labels = None
depends_on = None

ALLOWED_VARS = {"DB_APP_USER": r"^[a-z_][a-z0-9_]{0,62}$"}


def render(sql: str) -> str:
    def sub(m):
        name = m.group(1)
        if name not in ALLOWED_VARS:
            raise RuntimeError(f"template variable not allowed: {name}")
        val = os.environ.get(name, "")
        if not re.fullmatch(ALLOWED_VARS[name], val):
            raise RuntimeError(f"invalid value for {name}")
        return val
    return re.sub(r"\{\{([A-Z_]+)\}\}", sub, sql)


def _raw(sql: str) -> None:
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


UP = """
GRANT DELETE ON transcript_segments TO "{{DB_APP_USER}}";
"""

DOWN = """
REVOKE DELETE ON transcript_segments FROM "{{DB_APP_USER}}";
"""


def upgrade() -> None:
    _raw(render(UP))


def downgrade() -> None:
    _raw(render(DOWN))
