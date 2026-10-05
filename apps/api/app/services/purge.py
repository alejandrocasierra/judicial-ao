"""Purga total de un expediente (módulo Procesos).

Elimina TODAS las huellas del proceso en la base de datos —archivos (Excel,
Word, PDF, imágenes, videos), carpetas y subcarpetas, páginas OCR, segmentos
ASR, chunks y embeddings (pgvector), knowledge graph, extracción jurídica,
jobs, reviews y entradas de auditoría del caso— y después sus objetos en
storage. Sólo queda una fila "tumba" en audit_logs: quién purgó qué radicado
y cuándo (trazabilidad mínima de la propia eliminación).

La operación corre en una única transacción con la bandera
`app.allow_evidence_purge=on`: es la única vía por la que los triggers de
inmutabilidad (`protect_originals`, `protect_case_delete`, `audit_append_only`)
permiten el DELETE. Fuera de este servicio, la evidencia sigue siendo inmutable.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import rows
from app.services import audit
from app.services.storage import storage

log = logging.getLogger(__name__)

# Tablas con ids propios del caso: se recolectan para barrer sus entradas de auditoría.
_ID_TABLES = ("documents", "media", "jobs", "claims", "facts", "events", "decisions", "parties",
              "speakers", "entities", "contradictions", "evidence", "issues", "reviews",
              "model_runs", "case_folders", "case_files")

# Orden de borrado (hijos antes que padres). {case} = filtro por case_id,
# {docs} / {media} = subconsulta por ids del caso.
_DELETE_STEPS: list[tuple[str, str]] = [
    ("fact_claims", "DELETE FROM fact_claims WHERE fact_id IN (SELECT id FROM facts WHERE case_id = :c)"),
    ("evidence_links", "DELETE FROM evidence_links WHERE evidence_id IN (SELECT id FROM evidence WHERE case_id = :c)"),
    ("citations", "DELETE FROM citations WHERE case_id = :c"),
    ("contradictions", "DELETE FROM contradictions WHERE case_id = :c"),
    ("claims", "DELETE FROM claims WHERE case_id = :c"),
    ("facts", "DELETE FROM facts WHERE case_id = :c"),
    ("evidence", "DELETE FROM evidence WHERE case_id = :c"),
    ("events", "DELETE FROM events WHERE case_id = :c"),
    ("decisions", "DELETE FROM decisions WHERE case_id = :c"),
    ("issues", "DELETE FROM issues WHERE case_id = :c"),
    ("reviews", "DELETE FROM reviews WHERE case_id = :c"),
    ("model_runs", "DELETE FROM model_runs WHERE case_id = :c"),
    ("jobs", "DELETE FROM jobs WHERE case_id = :c"),
    ("chunks", "DELETE FROM chunks WHERE case_id = :c"),
    ("graph_edges", "DELETE FROM graph_edges WHERE case_id = :c"),
    ("graph_nodes", "DELETE FROM graph_nodes WHERE case_id = :c"),
    ("transcript_segments", "DELETE FROM transcript_segments WHERE media_id IN (SELECT id FROM media WHERE case_id = :c)"),
    ("speakers", "DELETE FROM speakers WHERE case_id = :c"),
    ("entities", "DELETE FROM entities WHERE case_id = :c"),
    ("parties", "DELETE FROM parties WHERE case_id = :c"),
    ("document_pages", "DELETE FROM document_pages WHERE document_id IN (SELECT id FROM documents WHERE case_id = :c)"),
    ("documents", "DELETE FROM documents WHERE case_id = :c"),
    ("media", "DELETE FROM media WHERE case_id = :c"),
    ("case_files", "DELETE FROM case_files WHERE case_id = :c"),
    ("case_members", "DELETE FROM case_members WHERE case_id = :c"),
]


def purge_case(c: Connection, org_id: str, case_id: str, actor_id: str, case: dict) -> dict:
    """Borra el expediente completo dentro de la transacción abierta `c`.
    Devuelve estadísticas de filas eliminadas por tabla."""
    c.execute(text("SET LOCAL app.allow_evidence_purge = 'on'"))

    # Ids del caso para el barrido de auditoría (antes de borrar nada).
    entity_ids: list[str] = [case_id]
    for table in _ID_TABLES:
        entity_ids.extend(str(r["id"]) for r in rows(c, f"SELECT id FROM {table} WHERE case_id = :c", c=case_id))

    stats: dict[str, int] = {}
    for table, sql in _DELETE_STEPS:
        stats[table] = c.execute(text(sql), {"c": case_id}).rowcount

    # Carpetas (auto-referencia parent_id: se borran de hojas a raíz).
    folders = 0
    while True:
        n = c.execute(text("""DELETE FROM case_folders f WHERE f.case_id = :c
                              AND NOT EXISTS (SELECT 1 FROM case_folders ch WHERE ch.parent_id = f.id)"""),
                      {"c": case_id}).rowcount
        folders += n
        if n == 0:
            break
    stats["case_folders"] = folders

    # Auditoría del caso: fuera todo rastro; queda sólo la tumba (abajo).
    stats["audit_logs"] = c.execute(
        text("DELETE FROM audit_logs WHERE organization_id = :o AND entity_id = ANY(:ids)"),
        {"o": org_id, "ids": entity_ids}).rowcount

    # El propio expediente.
    stats["cases"] = c.execute(text("DELETE FROM cases WHERE id = :c"), {"c": case_id}).rowcount

    # Tumba: constancia de quién eliminó qué (trazabilidad mínima de la purga).
    audit.record(c, org_id=org_id, actor_id=actor_id, action="case.purged", entity_type="case",
                 entity_id=case_id, before={"case_number": case["case_number"], "title": case["title"]},
                 after={"rows_deleted": stats})
    return stats


def purge_storage(case_id: str) -> int:
    """Borra los objetos del expediente en storage (originales, imágenes de
    página, media, archivos). Best-effort fuera de la transacción."""
    try:
        return storage().delete_prefix(f"cases/{case_id}/")
    except Exception:
        log.exception("no se pudo borrar el storage del caso %s", case_id)
        return 0
