"""Eliminación INMEDIATA de un archivo (documento o media) con cascada.

A diferencia de `deletion-request` (que solo MARCA la fila), esto borra de verdad:
- citas que apuntan al archivo (`citations`),
- chunks y embeddings en pgvector (`chunks`),
- páginas OCR y versiones por motor (`document_pages`, `document_ocr_versions`),
- segmentos de transcripción (`transcript_segments`),
- la fila de `documents`/`media`,
- el objeto original y las imágenes de página en storage.

Los triggers de inmutabilidad exigen `SET LOCAL app.allow_evidence_purge = 'on'`
(los delitos de evidencia siguen protegidos fuera de este contexto). El legal hold
se respeta: el trigger lanza `LEGAL_HOLD_ACTIVE` y la API lo convierte en 409.

El grafo del caso se reconstruye después (job `graph_build`), de modo que nodos y
aristas del archivo desaparecen.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import one
from app.services.storage import incoming_dir, key_from_uri, storage

log = logging.getLogger(__name__)


def delete_document(conn: Connection, org_id: str, case_id: str, document_id: str) -> dict[str, Any]:
    """Borra un documento y todo lo derivado. Devuelve las claves de storage a liberar."""
    doc = one(conn, "SELECT id, storage_uri FROM documents WHERE id = :d AND case_id = :c",
              d=document_id, c=case_id)
    if doc is None:
        raise ValueError("document_not_found")
    conn.execute(text("SET LOCAL app.allow_evidence_purge = 'on'"))
    # Derivados directos (pgvector + OCR + citas + progreso).
    conn.execute(text("DELETE FROM citations WHERE case_id = :c AND document_id = :d"),
                 {"c": case_id, "d": document_id})
    conn.execute(text("DELETE FROM chunks WHERE case_id = :c AND document_id = :d"),
                 {"c": case_id, "d": document_id})
    conn.execute(text("DELETE FROM document_ocr_progress WHERE document_id = :d"), {"d": document_id})
    conn.execute(text("DELETE FROM document_ocr_versions WHERE document_id = :d"), {"d": document_id})
    conn.execute(text("DELETE FROM document_pages WHERE document_id = :d"), {"d": document_id})
    # FKs a documents: se desvincula lo opcional y se borra lo obligatorio.
    conn.execute(text("UPDATE events SET document_id = NULL WHERE document_id = :d"), {"d": document_id})
    # evidence exige al menos una fuente (CHECK): si la fuente es el documento, se borra
    # la evidencia derivada (con sus enlaces) en vez de desvincularla.
    conn.execute(text("DELETE FROM evidence_links WHERE evidence_id IN "
                      "(SELECT id FROM evidence WHERE source_document_id = :d)"), {"d": document_id})
    conn.execute(text("DELETE FROM evidence WHERE source_document_id = :d"), {"d": document_id})
    conn.execute(text("UPDATE facts SET determined_by_decision_id = NULL "
                      "WHERE determined_by_decision_id IN "
                      "(SELECT id FROM decisions WHERE source_document_id = :d)"), {"d": document_id})
    conn.execute(text("DELETE FROM decisions WHERE source_document_id = :d"), {"d": document_id})
    conn.execute(text("DELETE FROM documents WHERE id = :d AND case_id = :c"),
                 {"d": document_id, "c": case_id})
    return {
        "kind": "document",
        "id": document_id,
        "storage_uri": doc["storage_uri"],
        "page_prefix": f"cases/{case_id}/pages/{document_id}/",
    }


def delete_media(conn: Connection, org_id: str, case_id: str, media_id: str) -> dict[str, Any]:
    """Borra un video/audio y todo lo derivado (segmentos, chunks, citas)."""
    m = one(conn, "SELECT id, storage_uri FROM media WHERE id = :m AND case_id = :c",
            m=media_id, c=case_id)
    if m is None:
        raise ValueError("media_not_found")
    conn.execute(text("SET LOCAL app.allow_evidence_purge = 'on'"))
    conn.execute(text("DELETE FROM citations WHERE case_id = :c AND media_id = :m"),
                 {"c": case_id, "m": media_id})
    conn.execute(text("DELETE FROM chunks WHERE case_id = :c AND media_id = :m"),
                 {"c": case_id, "m": media_id})
    conn.execute(text("DELETE FROM transcript_segments WHERE media_id = :m"), {"m": media_id})
    conn.execute(text("DELETE FROM media_asr_progress WHERE media_id = :m"), {"m": media_id})
    # evidence derivada del audio/video: se borra (con sus enlaces) por el CHECK de fuente.
    conn.execute(text("DELETE FROM evidence_links WHERE evidence_id IN "
                      "(SELECT id FROM evidence WHERE source_media_id = :m)"), {"m": media_id})
    conn.execute(text("DELETE FROM evidence WHERE source_media_id = :m"), {"m": media_id})
    conn.execute(text("DELETE FROM media WHERE id = :m AND case_id = :c"),
                 {"m": media_id, "c": case_id})
    return {"kind": "media", "id": media_id, "storage_uri": m["storage_uri"]}


def delete_storage(info: dict[str, Any]) -> None:
    """Libera los objetos del archivo en storage (best-effort, fuera de la transacción)."""
    try:
        uri = info.get("storage_uri")
        if uri:
            key = key_from_uri(str(uri))
            storage().delete(key)
            # Copia local temporal (incoming/<sha>) compartida api<->worker, si aún existe.
            try:
                (incoming_dir() / key.rsplit("/", 1)[-1]).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                log.debug("no se pudo borrar la copia local de %s", info.get("id"))
        prefix = info.get("page_prefix")
        if prefix:
            storage().delete_prefix(str(prefix))
    except Exception:  # noqa: BLE001 — el borrado en BD ya ocurrió; storage es best-effort
        log.exception("no se pudo borrar el storage de %s", info.get("id"))
