"""Hablantes: fusión de duplicados (la misma persona detectada en dos clusters de diarización)."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import rows


def merge(conn: Connection, case_id: str, keep_id: str, merge_id: str) -> list[str]:
    """Reasigna los segmentos de `merge_id` a `keep_id` y borra el hablante duplicado.

    No confirma la transacción (lo hace el llamador). Devuelve los media_ids afectados
    (los que tienen segmentos del hablante conservado) para reindexar en pgvector.
    """
    conn.execute(text("UPDATE transcript_segments SET speaker_id = :k WHERE speaker_id = :m"),
                 {"k": keep_id, "m": merge_id})
    conn.execute(text("DELETE FROM speakers WHERE id = :m AND case_id = :c"),
                 {"m": merge_id, "c": case_id})
    return [str(r["media_id"]) for r in rows(
        conn, "SELECT DISTINCT media_id FROM transcript_segments WHERE speaker_id = :k", k=keep_id)]
