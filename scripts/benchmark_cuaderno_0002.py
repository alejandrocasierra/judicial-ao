#!/usr/bin/env python3
r"""Procesa todo el cuaderno 0002 con el proveedor OCR activo y reporta métricas.

Uso:
    .venv\Scripts\python scripts\benchmark_cuaderno_0002.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402
from app.providers.ocr import get_ocr_provider  # noqa: E402
from app.services.document_pipeline import detect_folio  # noqa: E402

CUADERNO_DIR = ROOT / "11001310302120180036100" / "01PrimeraInstancia" / "0002 MedidasCautelares2018-361"
OUTPUT_JSON = ROOT / "var" / "benchmark_cuaderno_0002.json"


def process_pdf(path: Path, provider, threshold: float) -> dict:
    with fitz.open(path) as doc:
        total_pages = doc.page_count
    print(f"  {path.name}: {total_pages} páginas")
    document_bytes = path.read_bytes()
    t0 = time.perf_counter()
    pages = provider.process(document_bytes, "application/pdf")
    elapsed = time.perf_counter() - t0
    confidences = [p.confidence for p in pages]
    low_conf = sum(1 for c in confidences if c < threshold)
    folios = [detect_folio(p.text) for p in pages]
    return {
        "filename": path.name,
        "pages": len(pages),
        "elapsed_seconds": round(elapsed, 2),
        "seconds_per_page": round(elapsed / len(pages), 2) if pages else 0,
        "confidence_mean": round(statistics.mean(confidences), 3) if confidences else 0,
        "confidence_median": round(statistics.median(confidences), 3) if confidences else 0,
        "confidence_min": round(min(confidences), 3) if confidences else 0,
        "confidence_max": round(max(confidences), 3) if confidences else 0,
        "under_threshold_count": low_conf,
        "under_threshold_pct": round(100 * low_conf / len(pages), 1) if pages else 0,
        "folios_detected": sum(1 for f in folios if f),
    }


def main() -> None:
    s = get_settings()
    provider = get_ocr_provider()
    print(f"Cuaderno 0002 — proveedor: {provider.name}")
    print(f"DPI: {s.OCR_DPI}, preprocess: {s.OCR_PREPROCESS}, threshold: {s.OCR_CONFIDENCE_THRESHOLD}\n")

    results = []
    for pdf in sorted(CUADERNO_DIR.glob("*.pdf")):
        results.append(process_pdf(pdf, provider, s.OCR_CONFIDENCE_THRESHOLD))

    total_pages = sum(r["pages"] for r in results)
    total_elapsed = sum(r["elapsed_seconds"] for r in results)
    total_low = sum(r["under_threshold_count"] for r in results)
    total_folios = sum(r["folios_detected"] for r in results)

    summary = {
        "cuaderno": "0002 MedidasCautelares2018-361",
        "provider": provider.name,
        "dpi": s.OCR_DPI,
        "preprocess": s.OCR_PREPROCESS,
        "threshold": s.OCR_CONFIDENCE_THRESHOLD,
        "total_pdfs": len(results),
        "total_pages": total_pages,
        "total_elapsed_seconds": round(total_elapsed, 2),
        "seconds_per_page_mean": round(total_elapsed / total_pages, 2) if total_pages else 0,
        "under_threshold_total": total_low,
        "under_threshold_pct": round(100 * total_low / total_pages, 1) if total_pages else 0,
        "folios_detected_total": total_folios,
        "files": results,
    }

    print("\n=== Resumen cuaderno 0002 ===")
    print(f"PDFs: {summary['total_pdfs']}")
    print(f"Páginas: {summary['total_pages']}")
    print(f"Tiempo total: {summary['total_elapsed_seconds']} s")
    print(f"Tiempo/página: {summary['seconds_per_page_mean']} s")
    print(f"Bajo umbral: {total_low} ({summary['under_threshold_pct']}%)")
    print(f"Folios detectados: {total_folios}")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReporte guardado en: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
