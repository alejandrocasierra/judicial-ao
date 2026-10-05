"""Reprocesa páginas de baja confianza probando múltiples DPI y preprocesamientos.

Selecciona automáticamente el mejor resultado entre:
- RapidOCR 300 DPI + agresivo
- RapidOCR 400 DPI + agresivo
- RapidOCR 300 DPI + super-agresivo
- RapidOCR 400 DPI + super-agresivo
- RapidOCR 300 DPI sin preprocesar (baseline)
- RapidOCR 400 DPI sin preprocesar
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
from app.services.image_preprocessing import preprocess_aggressive, preprocess_super_aggressive
from app.services.storage import key_from_uri, storage

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _rapidocr_ocr(image: Image.Image, engine: RapidOCR) -> tuple[float, str, list[dict]]:
    output = engine(np.array(image))
    words: list[dict] = []
    if output is None or output.boxes is None:
        return 0.0, "", words
    for box, word_text, score in zip(output.boxes, output.txts, output.scores, strict=True):
        if not word_text.strip():
            continue
        points = box.tolist() if hasattr(box, "tolist") else box
        words.append({"text": word_text, "confidence": float(score), "bbox": {"points": points}})
    full_text = " ".join(output.txts)
    confidence = sum(float(s) for s in output.scores) / len(output.scores) if output.scores else 0.0
    return confidence, full_text, words


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
    ap.add_argument("--confidence-below", type=float, default=0.85)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = get_settings()
    rapidocr = RapidOCR()
    report: list[dict] = []

    with tx(args.org_id, args.user_id) as conn:
        page_rows = conn.execute(text("""
            SELECT dp.id AS page_id, dp.document_id, dp.page_number, dp.ocr_confidence, dp.text,
                   d.storage_uri, d.filename
            FROM document_pages dp
            JOIN documents d ON d.id = dp.document_id
            WHERE d.case_id = :c AND dp.ocr_confidence < :threshold
            ORDER BY dp.ocr_confidence ASC
        """), {"c": args.case_id, "threshold": args.confidence_below}).mappings().all()

        log.info("Páginas a reprocesar: %d", len(page_rows))

        doc_cache: dict[str, bytes] = {}

        for row in page_rows:
            doc_id = row["document_id"]
            page_number = row["page_number"]
            old_conf = float(row["ocr_confidence"])
            old_text = row["text"] or ""

            if doc_id not in doc_cache:
                key = key_from_uri(row["storage_uri"])
                doc_cache[doc_id] = storage().get(key)

            candidates: list[dict] = []
            for dpi in [300, 400]:
                image = _render_page(doc_cache[doc_id], page_number, dpi)
                # Baseline sin preprocesar
                conf, txt, words = _rapidocr_ocr(image, rapidocr)
                candidates.append({
                    "method": f"rapidocr_{dpi}_baseline",
                    "confidence": conf,
                    "text": txt,
                    "words": words,
                })
                for prep_name, prep_fn in [
                    ("aggr", preprocess_aggressive),
                    ("super", preprocess_super_aggressive),
                ]:
                    proc_image = prep_fn(image)
                    conf, txt, words = _rapidocr_ocr(proc_image, rapidocr)
                    candidates.append({
                        "method": f"rapidocr_{dpi}_{prep_name}",
                        "confidence": conf,
                        "text": txt,
                        "words": words,
                    })

            # Elige el mejor: mayor confianza, pero descarta textos demasiado cortos.
            best = max(
                (c for c in candidates if len(c["text"]) >= len(old_text) * 0.5),
                key=lambda c: c["confidence"],
                default=None,
            )
            if best is None:
                best = max(candidates, key=lambda c: c["confidence"])

            accepted = best["confidence"] > old_conf
            new_conf = best["confidence"]
            new_text = best["text"]

            entry = {
                "document_id": str(doc_id),
                "filename": row["filename"],
                "page_number": page_number,
                "old_confidence": old_conf,
                "new_confidence": new_conf,
                "method": best["method"],
                "accepted": accepted,
                "all_methods": [{"method": c["method"], "confidence": c["confidence"], "text_sample": c["text"][:120]} for c in candidates],
                "old_text_sample": old_text[:200],
                "new_text_sample": new_text[:200],
            }
            report.append(entry)

            if accepted:
                log.info("ACEPTADO %s pág %d: %.3f -> %.3f (%s)", row["filename"], page_number, old_conf, new_conf, best["method"])
                if not args.dry_run:
                    folio = detect_folio(new_text)
                    layout = {"words": best["words"][:100]} if best["words"] else None
                    conn.execute(text("""
                        UPDATE document_pages
                        SET text = :t,
                            ocr_confidence = :c,
                            folio = :f,
                            needs_review = :r,
                            layout_json = :l
                        WHERE id = :pid
                    """), {
                        "t": new_text,
                        "c": new_conf,
                        "f": folio,
                        "r": new_conf < s.OCR_CONFIDENCE_THRESHOLD,
                        "l": _serialize_layout(layout),
                        "pid": row["page_id"],
                    })
            else:
                log.info("RECHAZADO %s pág %d: mejor=%.3f no supera actual=%.3f", row["filename"], page_number, new_conf, old_conf)

    report_path = Path("var/super_reprocess_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Reporte guardado en %s", report_path)


if __name__ == "__main__":
    main()
