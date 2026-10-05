"""Exportación CKP (Case Knowledge Package): ZIP con manifest + JSON/JSONL por entidad + grafo + audit (SSD §36/§141)."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows
from app.services import markdown

CKP_VERSION = "1.0"


def _json_default(obj):
    """Serializador JSON para tipos no estándar."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _jsonl(items: list[dict]) -> bytes:
    """Serializa una lista de dicts a JSON Lines."""
    return "\n".join(json.dumps(it, ensure_ascii=False, default=_json_default) for it in items).encode("utf-8")


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def export_case(conn: Connection, case_id: UUID) -> bytes:
    """Genera el CKP completo de un expediente."""
    s = get_settings()
    cid = str(case_id)

    # Manifest (SSD §36)
    stats = one(conn, """
        SELECT
          (SELECT count(*) FROM documents WHERE case_id = :c) AS documents,
          (SELECT count(*) FROM document_pages p JOIN documents d ON d.id = p.document_id WHERE d.case_id = :c) AS pages,
          (SELECT count(*) FROM media WHERE case_id = :c) AS media_files,
          (SELECT coalesce(sum(m.duration_ms), 0) / 3600000.0 FROM media m WHERE m.case_id = :c) AS media_hours,
          (SELECT count(*) FROM chunks WHERE case_id = :c) AS chunks
    """, c=cid)

    manifest = {
        "package_version": CKP_VERSION,
        "case_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_files": stats["documents"] + stats["media_files"],
        "pages": stats["pages"],
        "media_files": stats["media_files"],
        "media_hours": round(float(stats["media_hours"]), 2),
        "chunks": stats["chunks"],
        "pipeline_version": s.PIPELINE_VERSION,
        "schema_version": s.SCHEMA_VERSION,
    }

    # Caso
    case = one(conn, "SELECT * FROM cases WHERE id = :c", c=cid)
    files: dict[str, bytes] = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        "case.json": json.dumps(dict(case), ensure_ascii=False, indent=2, default=_json_default).encode("utf-8"),
    }

    # Entidades principales (JSONL)
    files["entities/parties.jsonl"] = _jsonl(rows(conn, "SELECT * FROM parties WHERE case_id = :c", c=cid))
    files["entities/entities.jsonl"] = _jsonl(rows(conn, "SELECT * FROM entities WHERE case_id = :c", c=cid))
    files["entities/decisions.jsonl"] = _jsonl(rows(conn, "SELECT * FROM decisions WHERE case_id = :c", c=cid))
    files["entities/events.jsonl"] = _jsonl(rows(conn, "SELECT * FROM events WHERE case_id = :c", c=cid))
    files["entities/claims.jsonl"] = _jsonl(rows(conn, "SELECT * FROM claims WHERE case_id = :c", c=cid))
    files["entities/facts.jsonl"] = _jsonl(rows(conn, "SELECT * FROM facts WHERE case_id = :c", c=cid))
    files["entities/evidence.jsonl"] = _jsonl(rows(conn, "SELECT * FROM evidence WHERE case_id = :c", c=cid))
    files["entities/contradictions.jsonl"] = _jsonl(rows(conn, "SELECT * FROM contradictions WHERE case_id = :c", c=cid))
    files["entities/legal_rules.jsonl"] = _jsonl(rows(conn, """
        SELECT lr.* FROM legal_rules lr
        WHERE lr.organization_id = (SELECT organization_id FROM cases WHERE id = :c)
        AND lr.jurisdiction = (SELECT jurisdiction FROM cases WHERE id = :c)""", c=cid))
    files["entities/issues.jsonl"] = _jsonl(rows(conn, "SELECT * FROM issues WHERE case_id = :c", c=cid))
    files["entities/speakers.jsonl"] = _jsonl(rows(conn, "SELECT * FROM speakers WHERE case_id = :c", c=cid))

    # Documentos y media
    files["documents/documents.jsonl"] = _jsonl(rows(conn, "SELECT * FROM documents WHERE case_id = :c", c=cid))
    files["documents/document_pages.jsonl"] = _jsonl(rows(conn, """
        SELECT p.* FROM document_pages p
        JOIN documents d ON d.id = p.document_id
        WHERE d.case_id = :c""", c=cid))
    files["media/media.jsonl"] = _jsonl(rows(conn, "SELECT * FROM media WHERE case_id = :c", c=cid))
    files["media/transcript_segments.jsonl"] = _jsonl(rows(conn, """
        SELECT s.* FROM transcript_segments s
        JOIN media m ON m.id = s.media_id
        WHERE m.case_id = :c""", c=cid))

    # Markdown por documento (representación legible/semántica: RAW → DOCUMENT → SEMANTIC)
    for d in rows(conn, "SELECT id FROM documents WHERE case_id = :c ORDER BY created_at", c=cid):
        md = markdown.document_markdown(conn, cid, str(d["id"]))
        if md:
            files[f"documents/markdown/{d['id']}.md"] = md.encode("utf-8")

    # Chunks: unidades de recuperación (texto + metadatos + procedencia). Se omite el
    # vector (grande y regenerable con indexación).
    files["chunks/chunks.jsonl"] = _jsonl(rows(conn, """
        SELECT id, chunk_type, document_id, page_number, media_id, start_ms, end_ms, text, metadata,
               embedding_model, embedding_version, (embedding IS NOT NULL) AS has_embedding, created_at
        FROM chunks WHERE case_id = :c
        ORDER BY document_id, page_number, start_ms""", c=cid))

    # Citas y vínculos
    files["citations/citations.jsonl"] = _jsonl(rows(conn, "SELECT * FROM citations WHERE case_id = :c", c=cid))
    files["citations/evidence_links.jsonl"] = _jsonl(rows(conn, """
        SELECT el.* FROM evidence_links el
        JOIN evidence e ON e.id = el.evidence_id
        WHERE e.case_id = :c""", c=cid))
    files["citations/fact_claims.jsonl"] = _jsonl(rows(conn, """
        SELECT fc.* FROM fact_claims fc
        JOIN facts f ON f.id = fc.fact_id
        WHERE f.case_id = :c""", c=cid))

    # Grafo
    files["graph/nodes.jsonl"] = _jsonl(rows(conn, "SELECT * FROM graph_nodes WHERE case_id = :c", c=cid))
    files["graph/edges.jsonl"] = _jsonl(rows(conn, "SELECT * FROM graph_edges WHERE case_id = :c", c=cid))

    # Auditoría (SSD §141)
    # Auditoría: logs del caso y sus entidades (entity_id es text, puede ser cualquier tabla)
    files["audit/audit_logs.jsonl"] = _jsonl(rows(conn, """
        SELECT * FROM audit_logs
        WHERE organization_id = (SELECT organization_id FROM cases WHERE id = :c)
        AND (entity_id = CAST(:c AS text) OR entity_id IN (
            SELECT id::text FROM documents WHERE case_id = :c
            UNION ALL SELECT id::text FROM media WHERE case_id = :c
            UNION ALL SELECT id::text FROM claims WHERE case_id = :c
            UNION ALL SELECT id::text FROM facts WHERE case_id = :c
            UNION ALL SELECT id::text FROM jobs WHERE case_id = :c
            UNION ALL SELECT id::text FROM model_runs WHERE case_id = :c
        ))
        ORDER BY created_at""", c=cid))

    # Jobs y model runs
    files["jobs/jobs.jsonl"] = _jsonl(rows(conn, "SELECT * FROM jobs WHERE case_id = :c", c=cid))
    files["jobs/model_runs.jsonl"] = _jsonl(rows(conn, "SELECT * FROM model_runs WHERE case_id = :c", c=cid))

    return _zip_bytes(files)


def export_filename(case_id: UUID) -> str:
    return f"ckp-{case_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.zip"
