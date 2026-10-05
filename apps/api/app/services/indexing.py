"""Indexación de chunks: generación, embedding y persistencia en pgvector.

Expone funciones para reindexar un documento, un medio o todo un caso. La
reindexación es idempotente: borra los chunks previos del scope y los recrea.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

import json

from app.core.config import get_settings
from app.providers.embeddings import get_embedding_provider
from app.services import chunking

log = logging.getLogger(__name__)


def _batched(items: list[Any], n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


def _delete_chunks(conn: Connection, case_id: str, **filters) -> None:
    where = ["case_id = :c"]
    params: dict[str, Any] = {"c": case_id}
    for key, value in filters.items():
        if value is not None:
            where.append(f"{key} = :{key}")
            params[key] = value
    conn.execute(text("DELETE FROM chunks WHERE " + " AND ".join(where)), params)


def _insert_chunks(conn: Connection, chunks: list[dict[str, Any]], vectors: list[list[float]],
                   model: str, version: str) -> int:
    inserted = 0
    for ch, vec in zip(chunks, vectors, strict=False):
        conn.execute(text("""
            INSERT INTO chunks
              (organization_id, case_id, chunk_type, document_id, page_number, media_id,
               start_ms, end_ms, text, metadata, embedding, embedding_model, embedding_version)
            VALUES
              (:o, :c, :ct, :did, :pn, :mid, :sms, :ems, :text, CAST(:meta AS jsonb),
               CAST(:emb AS vector), :model, :version)
        """), {
            "o": ch["organization_id"],
            "c": ch["case_id"],
            "ct": ch["chunk_type"],
            "did": ch.get("document_id"),
            "pn": ch.get("page_number"),
            "mid": ch.get("media_id"),
            "sms": ch.get("start_ms"),
            "ems": ch.get("end_ms"),
            "text": ch["text"],
            "meta": json.dumps(ch["metadata"], ensure_ascii=False),
            "emb": _vector_literal(vec),
            "model": model,
            "version": version,
        })
        inserted += 1
    return inserted


def index_document(conn: Connection, org_id: str, case_id: str, document_id: str, actor_id: str | None = None) -> dict[str, Any]:
    """Genera chunks y embeddings para un documento."""
    provider = get_embedding_provider()
    _delete_chunks(conn, case_id, document_id=document_id)
    chunks = chunking.chunk_document(conn, org_id, case_id, document_id)
    count = _embed_and_insert(conn, chunks, provider)
    log.info("indexed document=%s chunks=%s", document_id, count)
    return {"document_id": document_id, "chunks": count}


def index_media(conn: Connection, org_id: str, case_id: str, media_id: str, actor_id: str | None = None) -> dict[str, Any]:
    provider = get_embedding_provider()
    _delete_chunks(conn, case_id, media_id=media_id)
    chunks = chunking.chunk_media(conn, org_id, case_id, media_id)
    count = _embed_and_insert(conn, chunks, provider)
    log.info("indexed media=%s chunks=%s", media_id, count)
    return {"media_id": media_id, "chunks": count}


def index_case(conn: Connection, org_id: str, case_id: str, actor_id: str | None = None) -> dict[str, Any]:
    provider = get_embedding_provider()
    _delete_chunks(conn, case_id)
    chunks = chunking.build_case_chunks(conn, org_id, case_id)
    count = _embed_and_insert(conn, chunks, provider)
    log.info("indexed case=%s chunks=%s", case_id, count)
    return {"case_id": case_id, "chunks": count}


def _embed_and_insert(conn: Connection, chunks: list[dict[str, Any]], provider: Any) -> int:
    if not chunks:
        return 0
    s = get_settings()
    batch_size = s.EMBEDDING_BATCH_SIZE
    model = s.EMBEDDING_MODEL
    version = s.PIPELINE_VERSION
    inserted = 0
    for batch in _batched(chunks, batch_size):
        texts = [ch["text"] for ch in batch]
        vectors = provider.embed(texts)
        inserted += _insert_chunks(conn, batch, vectors, model, version)
    return inserted


def index_structured(conn: Connection, org_id: str, case_id: str, actor_id: str | None = None) -> dict[str, Any]:
    """Reindexa solo los chunks provenientes de entidades extraídas (claims, events, facts, etc.)."""
    provider = get_embedding_provider()
    # Borrar solo chunks estructurados (no documentos ni media).
    conn.execute(text("""
        DELETE FROM chunks
        WHERE case_id = :c AND chunk_type IN ('claim','timeline_event','paragraph',
                                              'decision_reasoning','evidence_description','legal_rule')
    """), {"c": case_id})
    chunks: list[dict[str, Any]] = []
    chunks.extend(chunking.chunk_claims(conn, org_id, case_id))
    chunks.extend(chunking.chunk_events(conn, org_id, case_id))
    chunks.extend(chunking.chunk_facts(conn, org_id, case_id))
    chunks.extend(chunking.chunk_decisions(conn, org_id, case_id))
    chunks.extend(chunking.chunk_evidence(conn, org_id, case_id))
    count = _embed_and_insert(conn, chunks, provider)
    log.info("indexed structured case=%s chunks=%s", case_id, count)
    return {"case_id": case_id, "chunks": count}
