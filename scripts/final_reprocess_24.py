"""Último intento de reprocesamiento para las 24 páginas más difíciles.

Prueba configuraciones extremas:
- 500 DPI + baseline/agresivo/super-agresivo
- 400 DPI sin binarizar (solo CLAHE + bilateral + unsharp)
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
from app.services.image_preprocessing import (
    clahe,
    denoise_bilateral,
    preprocess_aggressive,
    preprocess_super_aggressive,
    unsharp_mask,
)
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


def _preprocess_no_binarize(image: Image.Image) -> Image.Image:
    """Mejora contraste/nitidez sin perder información por binarización."""
    image = image.convert("L").convert("RGB")
    image = clahe(image, clip=3.0, grid=12)
    image = denoise_bilateral(image, d=9, sigma=75)
    image = unsharp_mask(image, amount=1.5, radius=1.0)
    return image


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", required=True)
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
            WHERE d.case_id = :c AND dp.ocr_confidence < 0.85
            ORDER BY dp.ocr_confidence ASC
        """), {"c": args.case_id}).mappings().all()

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
            for dpi in [400, 500]:
                image = _render_page(doc_cache[doc_id], page_number, dpi)
                # Sin binarizar
                conf, txt, words = _rapidocr_ocr(_preprocess_no_binarize(image), rapidocr)
                candidates.append({"method": f"rapidocr_{dpi}_no_binarize", "confidence": conf, "text": txt, "words": words})
                # Agresivo
                conf, txt, words = _rapidocr_ocr(preprocess_aggressive(image), rapidocr)
                candidates.append({"method": f"rapidocr_{dpi}_aggr", "confidence": conf, "text": txt, "words": words})
                # Super-agresivo
                conf, txt, words = _rapidocr_ocr(preprocess_super_aggressive(image), rapidocr)
                candidates.append({"method": f"rapidocr_{dpi}_super", "confidence": conf, "text": txt, "words": words})

            best = max(
                (c for c in candidates if len(c["text"]) >= len(old_text) * 0.5),
                key=lambda c: c["confidence"],
                default=None,
            )
            if best is None:
                best = max(candidates, key=lambda c: c["confidence"])

            accepted = best["confidence"] > old_conf
            entry = {
                "document_id": str(doc_id),
                "filename": row["filename"],
                "page_number": page_number,
                "old_confidence": old_conf,
                "new_confidence": best["confidence"],
                "method": best["method"],
                "accepted": accepted,
                "old_text_sample": old_text[:200],
                "new_text_sample": best["text"][:200],
            }
            report.append(entry)

            if accepted:
                log.info("ACEPTADO %s pág %d: %.3f -> %.3f (%s)", row["filename"], page_number, old_conf, best["confidence"], best["method"])
                if not args.dry_run:
                    folio = detect_folio(best["text"])
                    layout = {"words": best["words"][:100]} if best["words"] else None
                    conn.execute(text("""
                        UPDATE document_pages
                        SET text = :t, ocr_confidence = :c, folio = :f,
                            needs_review = :r, layout_json = :l
                        WHERE id = :pid
                    """), {
                        "t": best["text"], "c": best["confidence"], "f": folio,
                        "r": best["confidence"] < s.OCR_CONFIDENCE_THRESHOLD,
                        "l": _serialize_layout(layout), "pid": row["page_id"],
                    })
            else:
                log.info("RECHAZADO %s pág %d: mejor=%.3f no supera actual=%.3f", row["filename"], page_number, best["confidence"], old_conf)

    report_path = Path("var/final_reprocess_24_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Reporte guardado en %s", report_path)


if __name__ == "__main__":
    main()
