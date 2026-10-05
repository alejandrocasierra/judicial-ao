#!/usr/bin/env python3
r"""Procesa todos los PDFs de un caso real con OCR (Docling por defecto).

Uso:
    .venv\Scripts\python scripts\process_all_pdfs.py \
        --case-id 182a09e0-deeb-4918-80f6-19cf350b4087 \
        --org-id b4e6d687-89b0-4b79-a0cc-69bd3532a7e9 \
        --user-id fc7ced6f-58e9-46c5-aa7c-b155c424c0fd

El script procesa documento por documento, guardando progreso en
var/process_all_pdfs.json para poder reanudar en caso de interrupción.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

load_dotenv(ROOT / ".env")

from app.core.config import get_settings  # noqa: E402
from app.core.db import tx  # noqa: E402
from app.services.document_pipeline import process_document  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROGRESS_FILE = ROOT / "var" / "process_all_pdfs.json"


def load_progress() -> tuple[set[str], int, list[dict]]:
    if not PROGRESS_FILE.exists():
        return set(), 0, []
    data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return (
        set(data.get("completed", [])),
        data.get("pages_total", 0),
        data.get("errors", []),
    )


def save_progress(
    case_id: str,
    completed: list[str],
    errors: list[dict],
    pages_total: int,
    current_document_id: str | None = None,
    current_document_filename: str | None = None,
) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "case_id": case_id,
        "completed": completed,
        "errors": errors,
        "pages_total": pages_total,
        "current_document_id": current_document_id,
        "current_document_filename": current_document_filename,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    # Escritura atómica para evitar corrupción si varias instancias escriben.
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROGRESS_FILE)


def get_connection() -> psycopg.Connection:
    s = get_settings()
    # Credenciales de superusuario no están en Settings por seguridad; leemos .env directamente.
    import os
    return psycopg.connect(
        host=s.POSTGRES_HOST, port=s.POSTGRES_PORT, dbname=s.POSTGRES_DB,
        user=os.environ["POSTGRES_SUPERUSER"], password=os.environ["POSTGRES_SUPERUSER_PASSWORD"]
    )


def get_pdf_documents(case_id: str) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, filename, mime_type FROM documents "
            "WHERE case_id = %s AND mime_type = 'application/pdf' "
            "ORDER BY orden_procesal NULLS LAST, filename",
            (case_id,)
        )
        return [{"id": str(r[0]), "filename": r[1], "mime_type": r[2]} for r in cur.fetchall()]
    finally:
        conn.close()


def reset_stuck_documents(case_id: str) -> int:
    """Pone en UPLOADED documentos que quedaron OCR_RUNNING/FAILED por corridas abortadas."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE documents SET processing_status = 'UPLOADED' "
            "WHERE case_id = %s AND processing_status IN ('OCR_RUNNING', 'FAILED')",
            (case_id,)
        )
        conn.commit()
        count = cur.rowcount
        log.info("Reseteados %d documentos en OCR_RUNNING/FAILED a UPLOADED", count)
        return count
    finally:
        conn.close()


def mark_document_failed(document_id: str) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE documents SET processing_status = 'FAILED' WHERE id = %s",
            (document_id,)
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--max-documents", type=int, default=0,
                    help="Detenerse después de procesar N documentos (0 = sin límite)")
    args = ap.parse_args()

    reset_stuck_documents(args.case_id)
    docs = get_pdf_documents(args.case_id)
    completed, pages_total, errors = load_progress()
    remaining = [d for d in docs if d["id"] not in completed]
    log.info("PDFs en caso: %d | ya procesados: %d | pendientes: %d | páginas_acum: %d",
             len(docs), len(completed), len(remaining), pages_total)

    t0_global = time.perf_counter()

    for i, doc in enumerate(remaining, start=1):
        # Escribe el documento en curso ANTES de empezar para poder diagnosticar caídas.
        save_progress(args.case_id, sorted(completed), errors, pages_total,
                      current_document_id=doc["id"], current_document_filename=doc["filename"])
        t0 = time.perf_counter()
        try:
            with tx(args.org_id, args.user_id) as conn:
                result = process_document(conn, doc["id"], args.org_id, args.case_id, args.user_id)
            elapsed = time.perf_counter() - t0
            pages_total += result.get("pages", 0)
            completed.add(doc["id"])
            log.info("[%d/%d] %s — págs=%d avg_conf=%.3f dt=%.2fs (total %.1fs)",
                     i, len(remaining), doc["filename"], result.get("pages", 0),
                     result.get("confidence_avg", 0), elapsed, time.perf_counter() - t0_global)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error procesando %s", doc["filename"])
            errors.append({"document_id": doc["id"], "filename": doc["filename"], "error": str(exc)})
            try:
                mark_document_failed(doc["id"])
            except Exception:
                log.exception("No se pudo marcar %s como FAILED", doc["filename"])

        # Guarda progreso tras cada documento para poder reanudar sin perder mucho.
        save_progress(args.case_id, sorted(completed), errors, pages_total)

        if args.max_documents and len(completed) >= args.max_documents:
            log.info("Alcanzado límite de %d documentos. Deteniendo batch.", args.max_documents)
            break

    save_progress(args.case_id, sorted(completed), errors, pages_total)
    log.info("FIN — procesados %d documentos, %d páginas, %d errores", len(completed), pages_total, len(errors))


if __name__ == "__main__":
    main()
