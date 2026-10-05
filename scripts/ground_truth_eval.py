#!/usr/bin/env python3
r"""Genera ground-truth editable y compara Tesseract vs Docling.

Uso:
    .venv\Scripts\python scripts\ground_truth_eval.py

Selecciona 50 páginas aleatorias del cuaderno principal, guarda:
  - var/ground_truth/<n>.png                (imagen renderizada)
  - var/ground_truth/ground_truth_template.json  (plantilla para transcripción manual)
  - var/ground_truth/relative_eval.json     (CER/WER de Tesseract vs Docling)

Para obtener métricas reales, edita ground_truth_template.json reemplazando
"manual": null por la transcripción humana y vuelve a correr el script.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pymupdf as fitz
from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.providers.ocr import DoclingOCR, TesseractOCR  # noqa: E402

PDF = ROOT / "11001310302120180036100" / "01PrimeraInstancia" / "0001 DemandaPrincipal2018-361" / "0001 CuadernoPrincipal2018-361.pdf"
OUT = ROOT / "var" / "ground_truth"
SAMPLE = 50
SEED = 42


def cer(hyp: str, ref: str) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    return Levenshtein.distance(hyp, ref) / len(ref)


def wer(hyp: str, ref: str) -> float:
    h = hyp.split()
    r = ref.split()
    if not r:
        return 0.0 if not h else 1.0
    return Levenshtein.distance(h, r) / len(r)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with fitz.open(PDF) as doc:
        total = doc.page_count
        random.seed(SEED)
        page_indices = sorted(random.sample(range(total), min(SAMPLE, total)))

        tess = TesseractOCR()
        docling = DoclingOCR()

        template = []
        relative = []
        for idx in page_indices:
            page = doc.load_page(idx)
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes("png")
            img_path = OUT / f"page_{idx+1:03d}.png"
            img_path.write_bytes(img_bytes)

            # Extrae OCR de ambos proveedores
            tess_pages = tess.process(img_bytes, "image/png")
            doc_pages = docling.process(img_bytes, "image/png")
            tess_text = tess_pages[0].text if tess_pages else ""
            doc_text = doc_pages[0].text if doc_pages else ""

            template.append({
                "page_index": idx + 1,
                "image": str(img_path),
                "tesseract": tess_text,
                "docling": doc_text,
                "manual": None,
            })
            relative.append({
                "page_index": idx + 1,
                "tesseract_vs_docling_cer": round(cer(tess_text, doc_text), 3),
                "tesseract_vs_docling_wer": round(wer(tess_text, doc_text), 3),
            })

    template_path = OUT / "ground_truth_template.json"
    template_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    relative_path = OUT / "relative_eval.json"
    relative_path.write_text(json.dumps(relative, ensure_ascii=False, indent=2), encoding="utf-8")

    avg_cer = sum(r["tesseract_vs_docling_cer"] for r in relative) / len(relative)
    avg_wer = sum(r["tesseract_vs_docling_wer"] for r in relative) / len(relative)

    print(f"Ground truth: {len(page_indices)} páginas en {OUT}")
    print(f"Template:     {template_path}")
    print(f"Relativo Tesseract-vs-Docling — CER={avg_cer:.3f} WER={avg_wer:.3f}")
    print("NOTA: para métricas reales reemplaza 'manual': null por la transcripción humana.")


if __name__ == "__main__":
    main()
