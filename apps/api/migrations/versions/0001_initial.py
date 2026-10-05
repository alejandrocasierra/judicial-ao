"""Esquema inicial: tablas, índices, triggers, funciones de auth, RLS y permisos.

Los archivos SQL son plantillas: {{VAR}} se reemplaza con variables de entorno
(DB_APP_USER, FTS_CONFIG, EMBEDDING_DIMENSIONS). Nada queda quemado.
"""
import os
import re
from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[2] / "db" / "sql"
FILES = ["010_schema.sql", "020_indexes.sql", "030_triggers.sql", "040_auth_functions.sql", "050_rls_grants.sql"]
ALLOWED_VARS = {"DB_APP_USER": r"^[a-z_][a-z0-9_]{0,62}$", "FTS_CONFIG": r"^[a-z_]+$", "EMBEDDING_DIMENSIONS": r"^\d{1,5}$"}


def render(sql: str) -> str:
    def sub(m):
        name = m.group(1)
        if name not in ALLOWED_VARS:
            raise RuntimeError(f"template variable not allowed: {name}")
        val = os.environ.get(name, "")
        if not re.fullmatch(ALLOWED_VARS[name], val):  # evita inyección SQL vía variables
            raise RuntimeError(f"invalid value for {name}")
        return val
    return re.sub(r"\{\{([A-Z_]+)\}\}", sub, sql)


def _raw(sql: str) -> None:
    # cursor DBAPI sin parámetros: el SQL (con '%' de format()) se envía tal cual
    with op.get_bind().connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def upgrade() -> None:
    for f in FILES:
        _raw(render((SQL_DIR / f).read_text(encoding="utf-8")))


def downgrade() -> None:
    app_user = render("{{DB_APP_USER}}")
    _raw(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{app_user}"; '
                         f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM "{app_user}"; '
                         f'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM "{app_user}";')
    tables = ["chunks", "audit_logs", "reviews", "model_runs", "jobs", "issues", "legal_rules", "contradictions", "citations",
              "evidence_links", "evidence", "fact_claims", "facts", "claims", "events", "decisions", "entities",
              "transcript_segments", "speakers", "media", "document_pages", "documents", "parties", "case_members",
              "cases", "refresh_tokens", "users", "organizations"]
    _raw("DROP TABLE IF EXISTS " + ", ".join(tables) + " CASCADE;")
    for fn in ["enforce_same_org()", "protect_originals()", "protect_case_delete()", "audit_chain()", "audit_append_only()",
               "touch_updated_at()", "fact_decision_same_case()", "auth_find_user(text)", "auth_register_failure(uuid,int,int)",
               "auth_register_success(uuid)", "current_org()"]:
        _raw(f"DROP FUNCTION IF EXISTS {fn} CASCADE;")
