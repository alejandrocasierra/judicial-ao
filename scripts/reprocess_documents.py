#!/usr/bin/env python3
r"""Reprocesamiento masivo de documentos con el nuevo proveedor OCR.

Uso:
    # Reprocesar todos los documentos de una organización
    .venv\Scripts\python scripts\reprocess_documents.py --org-id "b4e6d687-..."

    # Reprocesar sólo documentos de un caso
    .venv\Scripts\python scripts\reprocess_documents.py --org-id "..." --case-id "..."

    # Reprocesar con límite (para pruebas)
    .venv\Scripts\python scripts\reprocess_documents.py --org-id "..." --limit 10

    # Dry-run: muestra qué se haría sin ejecutar
    .venv\Scripts\python scripts\reprocess_documents.py --org-id "..." --dry-run

Requiere:
    - Variables de entorno en .env (BD, storage, OCR)
    - OCR_PROVIDER=docling_layout (o el que se quiera usar)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Asegura que apps/api esté en path.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))


from app.core.config import get_settings  # noqa: E402
from app.core.db import one, rows, tx  # noqa: E402
from app.services.document_pipeline import process_document  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reprocess")

DEFAULT_BATCH = 50


def get_documents(conn, org_id: str, case_id: str | None, limit: int | None) -> list[dict]:
    """Documentos candidatos a reprocesar: los que ya pasaron por OCR."""
    conditions = ["d.organization_id = :org", "d.processing_status IN ('OCR_COMPLETE', 'REVIEW_REQUIRED')"]
    params = {"org": org_id}
    if case_id:
        conditions.append("d.case_id = :case")
        params["case"] = case_id
    sql = f"""
        SELECT d.id, d.case_id, d.filename, d.processing_status, d.page_count,
               (SELECT count(*) FROM document_pages p WHERE p.document_id = d.id AND p.human_corrected) AS human_corrected_pages
        FROM documents d
        WHERE {' AND '.join(conditions)}
        ORDER BY d.created_at
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return rows(conn, sql, **params)


def reprocess_document(conn, doc: dict, org_id: str, dry_run: bool) -> dict:
    """Reprocesa un documento individual."""
    doc_id = str(doc["id"])
    case_id = str(doc["case_id"])
    result = {
        "document_id": doc_id,
        "filename": doc["filename"],
        "case_id": case_id,
        "status_before": doc["processing_status"],
        "human_corrected_pages": doc["human_corrected_pages"],
    }

    if dry_run:
        result["action"] = "would_reprocess"
        return result

    try:
        # Resetear estado para que process_document lo tome.
        one(conn, "UPDATE documents SET processing_status = 'OCR_PENDING' WHERE id = :d RETURNING id", d=doc_id)
        # Marcar páginas corregidas por humanos para no perderlas.
        protected = rows(conn, "SELECT page_number FROM document_pages WHERE document_id = :d AND human_corrected", d=doc_id)
        result["protected_pages"] = [r["page_number"] for r in protected]

        pipeline_result = process_document(conn, doc_id, org_id, case_id, "system-reprocess")
        result.update(pipeline_result)
        result["action"] = "reprocessed"
        result["status_after"] = "REVIEW_REQUIRED" if pipeline_result.get("needs_review_count") else "OCR_COMPLETE"
    except Exception as exc:
        log.exception("falló reproceso de documento %s", doc_id)
        result["action"] = "error"
        result["error"] = str(exc)[:500]

    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Reprocesamiento masivo de documentos con nuevo OCR")
    ap.add_argument("--org-id", required=True, help="ID de la organización")
    ap.add_argument("--case-id", help="ID del caso (opcional, si no se da usa todos)")
    ap.add_argument("--limit", type=int, help="Máximo de documentos a procesar")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, help="Tamaño del lote para commits")
    ap.add_argument("--dry-run", action="store_true", help="Simula sin ejecutar cambios")
    args = ap.parse_args()

    s = get_settings()
    log.info("Proveedor OCR configurado: %s", s.OCR_PROVIDER)
    log.info("Organización: %s", args.org_id)
    if args.case_id:
        log.info("Caso filtrado: %s", args.case_id)
    if args.limit:
        log.info("Límite: %s documentos", args.limit)
    if args.dry_run:
        log.info("MODO DRY-RUN: no se harán cambios reales")

    with tx(args.org_id, "system-reprocess") as conn:
        docs = get_documents(conn, args.org_id, args.case_id, args.limit)
        log.info("Documentos encontrados: %s", len(docs))

        if not docs:
            log.info("No hay documentos para reprocesar.")
            return

        stats = {"total": len(docs), "ok": 0, "error": 0, "skipped": 0, "would_reprocess": 0}
        t0 = time.perf_counter()

        for i, doc in enumerate(docs, 1):
            log.info("[%s/%s] %s (%s páginas, %s corregidas a mano)",
                     i, len(docs), doc["filename"], doc["page_count"], doc["human_corrected_pages"])
            result = reprocess_document(conn, doc, args.org_id, args.dry_run)
            stats[result["action"]] += 1
            if result["action"] == "error":
                log.error("  ERROR: %s", result.get("error"))

            # Commit por lote para no perder todo si falla a la mitad.
            if not args.dry_run and i % args.batch_size == 0:
                conn.commit()
                log.info("  Lote committeado (%s documentos)", i)

        if not args.dry_run:
            conn.commit()

    elapsed = time.perf_counter() - t0
    log.info("=== Resumen ===")
    log.info("Total: %s | OK: %s | Error: %s | Skip: %s | Dry-run: %s",
             stats["total"], stats["ok"], stats["error"], stats["skipped"], stats["would_reprocess"])
    log.info("Tiempo total: %.1f segundos", elapsed)
    log.info("Tiempo promedio por documento: %.2f segundos", elapsed / max(1, stats["total"]))

    if stats["error"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
