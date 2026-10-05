"""Utilidades para mover UN expediente entre instancias (sin reemplazar toda la BD).

- `export_case.py` vuelca las filas del caso (con sus UUIDs) + sus archivos locales.
- `import_case.py` las inserta en la instancia destino (idempotente: ON CONFLICT DO NOTHING).

Se usa el rol de la APP (RLS) fijando `app.current_org`, así que sólo se ve esa organización.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))
import envload  # noqa: E402

_env_file = Path(os.environ["ENV_FILE"]) if os.environ.get("ENV_FILE") else ROOT / ".env"
if not _env_file.is_absolute():
    _env_file = ROOT / _env_file
if _env_file.exists():
    envload.load(str(_env_file), override=True)

# Tablas de nivel organización (opcionales, para que el caso quede autocontenido).
ORG_TABLES = ["organizations", "users"]

# Tablas con columna case_id.
CASE_TABLES = [
    "case_members", "case_folders", "case_files", "parties", "documents", "media",
    "speakers", "entities", "claims", "facts", "events", "decisions", "evidence",
    "issues", "contradictions", "citations", "reviews", "chunks", "graph_nodes",
    "graph_edges", "chat_sessions", "jobs", "model_runs",
]

# Tablas hijas sin case_id (se exportan por join).
CHILD_TABLES = {
    "document_pages": "document_id IN (SELECT id FROM documents WHERE case_id = %(c)s)",
    "document_ocr_versions": "document_id IN (SELECT id FROM documents WHERE case_id = %(c)s)",
    "document_ocr_progress": "document_id IN (SELECT id FROM documents WHERE case_id = %(c)s)",
    "transcript_segments": "media_id IN (SELECT id FROM media WHERE case_id = %(c)s)",
    "fact_claims": "fact_id IN (SELECT id FROM facts WHERE case_id = %(c)s)",
    "evidence_links": "evidence_id IN (SELECT id FROM evidence WHERE case_id = %(c)s)",
    "chat_messages": "session_id IN (SELECT id FROM chat_sessions WHERE case_id = %(c)s)",
    "chat_pending_corrections": "session_id IN (SELECT id FROM chat_sessions WHERE case_id = %(c)s)",
}

EXPORT_WHERE: dict[str, str] = {
    "cases": "id = %(c)s",
    "organizations": "id = (SELECT organization_id FROM cases WHERE id = %(c)s)",
    "users": "organization_id = (SELECT organization_id FROM cases WHERE id = %(c)s)",
    **{t: "case_id = %(c)s" for t in CASE_TABLES},
    **CHILD_TABLES,
}

# Orden de inserción respetando FKs.
IMPORT_ORDER = [
    "organizations", "users", "cases",
    "case_members", "case_folders", "parties",
    "documents", "document_pages", "document_ocr_versions", "document_ocr_progress",
    "media", "case_files", "speakers", "transcript_segments",
    "entities", "claims", "decisions", "facts", "fact_claims", "events",
    "evidence", "evidence_links", "issues", "contradictions", "citations", "reviews",
    "chunks", "graph_nodes", "graph_edges",
    "model_runs", "chat_sessions", "chat_messages", "chat_pending_corrections", "jobs",
]


def connect(org_id: str | None = None):
    """Conexión de ADMINISTRACIÓN (superusuario), como pg_dump/pg_restore: así `COPY FROM`
    funciona (RLS lo bloquea para el rol de la app). `org_id` se conserva por compatibilidad."""
    import psycopg
    from app.core.config import get_settings

    s = get_settings()
    user = os.environ.get("POSTGRES_SUPERUSER") or os.environ.get("DB_OWNER_USER")
    password = os.environ.get("POSTGRES_SUPERUSER_PASSWORD") or os.environ.get("DB_OWNER_PASSWORD")
    return psycopg.connect(host=s.POSTGRES_HOST, port=s.POSTGRES_PORT, dbname=s.POSTGRES_DB,
                           user=user, password=password, autocommit=False)


def insertable_columns(cur, table: str) -> list[str]:
    cur.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
          AND is_generated = 'NEVER' AND is_identity = 'NO'
        ORDER BY ordinal_position""", (table,))
    return [r[0] for r in cur.fetchall()]


def export_table(cur, table: str, where: str, cols: list[str], out_file: Path, case_id: str) -> None:
    collist = ", ".join(f'"{c}"' for c in cols)
    sql = (f'COPY (SELECT {collist} FROM public."{table}" WHERE {where}) '
           f"TO STDOUT WITH (FORMAT csv, HEADER, NULL '\\N')")
    with open(out_file, "wb") as f, cur.copy(sql, {"c": case_id}) as cp:
        for block in cp:
            f.write(block)


def import_table(cur, table: str, cols: list[str], in_file: Path) -> int:
    """Inserta desde el CSV respetando NULL vs '' (COPY directo). Falla si hay conflicto
    (el import está pensado para un destino sin ese caso; org/usuarios se omiten si ya existen)."""
    collist = ", ".join(f'"{c}"' for c in cols)
    with open(in_file, "rb") as f, cur.copy(f'COPY public."{table}" ({collist}) FROM STDIN WITH (FORMAT csv, HEADER, NULL \'\\N\')') as cp:
        while True:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            cp.write(chunk)
    return cur.rowcount
