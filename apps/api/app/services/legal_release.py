"""Liberación humana de una respuesta retenida por segunda opinión.

La regla de producto es deliberadamente conservadora: el sistema no publica una
respuesta retenida automáticamente solo porque termine la segunda opinión. Un
revisor con permiso `audit.review` decide liberarla, y la decisión queda en
`audit_logs`.
"""
from __future__ import annotations

from typing import Any

from app.core.db import one


class ReleaseError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def release_held_message(conn, *, org_id: str, case_id: str, message_id: str,
                         reason: str, force: bool = False) -> dict[str, Any]:
    msg = one(conn, """
        SELECT m.id, m.publication_blocked, m.held_answer, m.held_citations,
               m.held_occurrences, m.content
        FROM chat_messages m
        JOIN chat_sessions s ON s.id = m.session_id
        WHERE m.id = :m AND s.case_id = :c AND m.organization_id = :o
          AND s.organization_id = :o
    """, m=message_id, c=case_id, o=org_id)
    if not msg:
        raise ReleaseError("MESSAGE_NOT_FOUND", "El mensaje no pertenece a este proceso.")
    if not msg.get("publication_blocked"):
        raise ReleaseError("MESSAGE_NOT_HELD", "El mensaje no está retenido.")
    if not msg.get("held_answer"):
        raise ReleaseError("HELD_ANSWER_MISSING", "No hay una respuesta original guardada para liberar.")

    run = one(conn, """
        SELECT id, status, verdict
        FROM second_opinion_runs
        WHERE organization_id = :o AND case_id = :c AND chat_message_id = :m
        ORDER BY created_at DESC
        LIMIT 1
    """, o=org_id, c=case_id, m=message_id)
    if not run:
        raise ReleaseError("SECOND_OPINION_REQUIRED", "Primero solicita la segunda opinión.")
    if run.get("status") != "COMPLETED":
        raise ReleaseError("SECOND_OPINION_PENDING",
                           "La segunda opinión aún no está completada.")
    if str(run.get("verdict") or "").upper() == "DISAGREE" and not force:
        raise ReleaseError("SECOND_OPINION_DISAGREES",
                           "La segunda opinión discrepa. La liberación exige confirmación explícita.")

    released = one(conn, """
        UPDATE chat_messages
        SET content = :answer,
            citations = CAST(:citations AS jsonb),
            occurrences = CAST(:occurrences AS jsonb),
            publication_blocked = false,
            publication_block_reason = null
        WHERE id = :m AND organization_id = :o AND publication_blocked
        RETURNING id, content, citations, occurrences, publication_blocked
    """, answer=msg["held_answer"],
         citations=msg.get("held_citations") or "[]",
         occurrences=msg.get("held_occurrences") or "[]",
         m=message_id, o=org_id)
    if not released:
        raise ReleaseError("MESSAGE_NOT_HELD", "El mensaje ya no está retenido.")
    return {"message": released, "run": run, "reason": reason}
