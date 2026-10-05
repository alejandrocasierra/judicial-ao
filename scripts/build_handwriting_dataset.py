#!/usr/bin/env python3
"""Construye el corpus de caligrafía (recorte de línea -> texto) desde las páginas
corregidas a mano (`document_pages.human_corrected`).

Salida (por defecto en `var/handwriting_dataset/`):
  - `images/*.png`     recortes de línea
  - `metadata.jsonl`   {"image": "images/....png", "text": "...", "document_id": ..., "page": ...}
  - `summary.json`     estadísticas (páginas, líneas, emparejadas)

Uso:
    .venv\\Scripts\\python scripts\\build_handwriting_dataset.py \\
        --org-id ... --user-id ... [--case-id ...] [--document-id ...] [--out var/handwriting_dataset]
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402
from app.core.db import rows, tx  # noqa: E402
from app.services.handwriting_dataset import build_pairs  # noqa: E402
from app.services.storage import key_from_uri, storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_handwriting_dataset")


def _ocr_lines(array: np.ndarray) -> tuple[list, list[str]]:
    """Detecta líneas con RapidOCR y devuelve (cajas, textos)."""
    from rapidocr import RapidOCR

    out = RapidOCR()(array)
    if out is None or out.boxes is None:
        return [], []
    return list(out.boxes), [t for t in out.txts]


def _crop(image: Image.Image, box) -> Image.Image:
    pts = box.tolist() if hasattr(box, "tolist") else box
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = max(0, int(min(xs)) - 2), max(0, int(min(ys)) - 2)
    x1, y1 = min(image.width, int(max(xs)) + 2), min(image.height, int(max(ys)) + 2)
    return image.crop((x0, y0, x1, y1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--case-id")
    ap.add_argument("--document-id")
    ap.add_argument("--out", default="var/handwriting_dataset")
    args = ap.parse_args()

    s = get_settings()
    where, params = ["p.human_corrected"], {}
    if args.document_id:
        where.append("p.document_id = :d")
        params["d"] = args.document_id
    if args.case_id:
        where.append("d.case_id = :c")
        params["c"] = args.case_id

    with tx(args.org_id, args.user_id) as conn:
        # Condiciones internas (literales), nunca entrada del usuario; los valores van atados.
        sql = ("SELECT p.id, p.document_id, p.page_number, p.text, d.storage_uri, "
               "d.mime_type, d.filename "
               "FROM document_pages p JOIN documents d ON d.id = p.document_id WHERE "
               + " AND ".join(where) + " ORDER BY p.document_id, p.page_number")
        pages = rows(conn, sql, **params)

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    meta: list[dict] = []
    docs_cache: dict[str, bytes] = {}

    for page in pages:
        doc_id = str(page["document_id"])
        if doc_id not in docs_cache:
            docs_cache[doc_id] = storage().get(key_from_uri(page["storage_uri"]))
        ftype = "pdf" if (page["mime_type"] or "").endswith("pdf") else "png"
        with fitz.open(stream=docs_cache[doc_id], filetype=ftype) as doc:
            pix = doc.load_page(page["page_number"] - 1).get_pixmap(dpi=s.OCR_DPI)
            image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

        boxes, detected = _ocr_lines(np.array(image))
        pairs = build_pairs(detected, page["text"] or "")
        made = 0
        for idx, (det_idx, label) in enumerate(pairs):
            name = f"{doc_id}_{page['page_number']:04d}_{idx:03d}.png"
            _crop(image, boxes[det_idx]).save(out / "images" / name)
            meta.append({"image": f"images/{name}", "text": label,
                         "document_id": doc_id, "page": page["page_number"],
                         "filename": page["filename"]})
            made += 1
        log.info("%s pág %d: %d líneas detectadas -> %d pares",
                 page["filename"], page["page_number"], len(detected), made)

    with (out / "metadata.jsonl").open("w", encoding="utf-8") as fh:
        for m in meta:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    summary = {"pages": len(pages), "lines": len(meta), "out": str(out)}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("FIN: %s", summary)


if __name__ == "__main__":
    main()
