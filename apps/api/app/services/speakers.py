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


def dedupe_by_name(conn: Connection, case_id: str) -> list[dict[str, str]]:
    """Fusiona hablantes del caso con el MISMO `display_name` resuelto (duplicados).

    pyannote puede partir a una misma persona en dos clusters (p. ej. SPEAKER_01 y
    SPEAKER_02) y la identificación visual / auto-presentación les asigna el mismo
    nombre: quedan DOS registros repetidos en la lista de hablantes y en el selector.
    Se conserva el mejor candidato (confirmado por humano > más segmentos > label
    menor) y se reasignan los segmentos del duplicado. No confirma la transacción.

    Devuelve la lista de {'keep', 'merged', 'name'} para auditoría.
    """
    items = rows(conn, """
        SELECT s.id, s.media_id, s.label, s.display_name, s.resolution_status, s.resolution_source,
               (SELECT count(*) FROM transcript_segments ts WHERE ts.speaker_id = s.id) AS segs
        FROM speakers s
        WHERE s.case_id = :c AND coalesce(btrim(s.display_name), '') <> ''
    """, c=case_id)
    groups: dict[tuple[str, str], list[dict]] = {}
    for it in items:
        key = (str(it["media_id"]), str(it["display_name"]).strip().lower())
        groups.setdefault(key, []).append(it)

    merged: list[dict[str, str]] = []
    for (_media, name), group in groups.items():
        if len(group) < 2:
            continue

        def _rank(s: dict) -> tuple[int, int, str]:
            human = s["resolution_status"] == "CONFIRMED" or s["resolution_source"] == "human"
            return (0 if human else 1, -int(s["segs"] or 0), str(s["label"] or ""))

        group.sort(key=_rank)
        keep = group[0]
        for dup in group[1:]:
            merge(conn, case_id, str(keep["id"]), str(dup["id"]))
            merged.append({"keep": str(keep["id"]), "merged": str(dup["id"]), "name": name})
    return merged
