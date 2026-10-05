"""Reprocesa páginas específicas con preprocesamiento agresivo para mejorar OCR.

Ejemplos:
    # Reprocesar páginas 9 y 55 de un documento
    python scripts/reprocess_pages_aggressive.py \
        --case-id ID --org-id ID --user-id ID \
        --document-id DOC_ID --pages 9 55

    # Reprocesar todas las páginas de un caso con confianza < 0.85
    python scripts/reprocess_pages_aggressive.py \
        --case-id ID --org-id ID --user-id ID \
        --confidence-below 0.85
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pymupdf as fitz
from PIL import Image
from rapidocr import RapidOCR
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.core.config import get_settings
from app.core.db import tx
from app.services.document_pipeline import _serialize_layout, detect_folio
from app.services.image_preprocessing import preprocess_aggressive
from app.services.storage import key_from_uri, storage

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _ocr_page(image: Image.Image, engine: RapidOCR) -> tuple[float, str, list[dict]]:
    output = engine(np.array(image))
    words: list[dict] = []
    for box, word_text, score in zip(output.boxes, output.txts, output.scores, strict=True):
        if not word_text.strip():
            continue
        points = box.tolist() if hasattr(box, "tolist") else box
        words.append({"text": word_text, "confidence": float(score), "bbox": {"points": points}})
    text = " ".join(output.txts)
    confidence = sum(float(s) for s in output.scores) / len(output.scores) if output.scores else 0.0
    return confidence, text, words


def _render_page(document_bytes: bytes, page_number: int, dpi: int) -> Image.Image:
    with fitz.open(stream=document_bytes, filetype="pdf") as doc:
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(dpi=dpi)
        return Image.open(io.BytesIO(pix.tobytes("png")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--document-id")
    ap.add_argument("--pages", type=int, nargs="*")
    ap.add_argument("--confidence-below", type=float, default=0.0,
                    help="Reprocesar automáticamente páginas bajo este umbral")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = get_settings()
    engine = RapidOCR()
    report: list[dict] = []

    with tx(args.org_id, args.user_id) as conn:
        if args.document_id and args.pages:
            page_rows = conn.execute(text("""
                SELECT dp.document_id, dp.page_number, dp.ocr_confidence, dp.text,
                       d.storage_uri, d.filename
                FROM document_pages dp
                JOIN documents d ON d.id = dp.document_id
                WHERE dp.document_id = :d AND dp.page_number = ANY(:pages)
            """), {"d": args.document_id, "pages": args.pages}).mappings().all()
        elif args.confidence_below:
            page_rows = conn.execute(text("""
                SELECT dp.document_id, dp.page_number, dp.ocr_confidence, dp.text,
                       d.storage_uri, d.filename
                FROM document_pages dp
                JOIN documents d ON d.id = dp.document_id
                WHERE d.case_id = :c AND dp.ocr_confidence < :threshold
                ORDER BY dp.ocr_confidence ASC
            """), {"c": args.case_id, "threshold": args.confidence_below}).mappings().all()
        else:
            ap.error("Debes indicar --document-id + --pages, o --confidence-below")
            return

        log.info("Páginas a reprocesar: %d", len(page_rows))

        # Cache de document_bytes por documento
        doc_cache: dict[str, bytes] = {}

        for row in page_rows:
            doc_id = row["document_id"]
            page_number = row["page_number"]
            old_conf = float(row["ocr_confidence"])
            old_text = row["text"] or ""

            if doc_id not in doc_cache:
                key = key_from_uri(row["storage_uri"])
                doc_cache[doc_id] = storage().get(key)

            image = _render_page(doc_cache[doc_id], page_number, s.OCR_DPI)
            aggressive_image = preprocess_aggressive(image)
            new_conf, new_text, words = _ocr_page(aggressive_image, engine)

            # Heurístico de aceptación: mejor confianza y no mucha pérdida de longitud.
            accepted = new_conf > old_conf and len(new_text) >= len(old_text) * 0.7

            entry = {
                "document_id": str(doc_id),
                "filename": row["filename"],
                "page_number": page_number,
                "old_confidence": old_conf,
                "new_confidence": new_conf,
                "accepted": accepted,
                "old_text_sample": old_text[:200],
                "new_text_sample": new_text[:200],
            }
            report.append(entry)

            if accepted:
                log.info("ACEPTADO %s pág %d: %.3f -> %.3f", row["filename"], page_number, old_conf, new_conf)
                if not args.dry_run:
                    folio = detect_folio(new_text)
                    layout = {"words": words[:100]} if words else None
                    conn.execute(text("""
                        UPDATE document_pages
                        SET text = :t,
                            ocr_confidence = :c,
                            folio = :f,
                            needs_review = :r,
                            layout_json = :l
                        WHERE document_id = :d AND page_number = :p
                    """), {
                        "t": new_text,
                        "c": new_conf,
                        "f": folio,
                        "r": new_conf < s.OCR_CONFIDENCE_THRESHOLD,
                        "l": _serialize_layout(layout),
                        "d": doc_id,
                        "p": page_number,
                    })
            else:
                log.info("RECHAZADO %s pág %d: %.3f -> %.3f", row["filename"], page_number, old_conf, new_conf)

    report_path = Path("var/reprocess_aggressive_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Reporte guardado en %s", report_path)


if __name__ == "__main__":
    main()
