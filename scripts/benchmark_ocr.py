#!/usr/bin/env python3
r"""Benchmark OCR comparativo sobre muestra real del cuaderno principal.

Uso:
    # Comparar todos los proveedores
    .venv\Scripts\python scripts\benchmark_ocr.py --compare-all

    # Probar uno específico
    .venv\Scripts\python scripts\benchmark_ocr.py --provider tesseract
    .venv\Scripts\python scripts\benchmark_ocr.py --provider docling
    .venv\Scripts\python scripts\benchmark_ocr.py --provider docling_layout

    # Con muestra personalizada
    .venv\Scripts\python scripts\benchmark_ocr.py --compare-all --sample 20

Requiere:
    - Variables OCR_* en .env
    - Para tesseract: binario + spa.traineddata
    - Para docling/docling_layout: docling + onnxruntime instalados

Muestra por defecto: primeras 50 páginas de:
    11001310302120180036100/01PrimeraInstancia/0001 DemandaPrincipal2018-361/0001 CuadernoPrincipal2018-361.pdf
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import pymupdf as fitz

# Asegura que apps/api esté en path para importar proveedores.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402
from app.providers.ocr import DoclingLayoutOCR, DoclingOCR, TesseractOCR  # noqa: E402
from app.services.document_pipeline import detect_folio  # noqa: E402

CUADERNO_PRINCIPAL = (
    ROOT
    / "11001310302120180036100"
    / "01PrimeraInstancia"
    / "0001 DemandaPrincipal2018-361"
    / "0001 CuadernoPrincipal2018-361.pdf"
)
DEFAULT_SAMPLE = 50
OUTPUT_JSON = ROOT / "var" / "benchmark_ocr.json"
COMPARE_JSON = ROOT / "var" / "benchmark_ocr_compare.json"


def build_sub_pdf(path: Path, start: int, end: int) -> bytes:
    with fitz.open(path) as src:
        with fitz.open() as dst:
            dst.insert_pdf(src, from_page=start, to_page=end - 1)
            return dst.tobytes()


PROVIDER_CLASSES = {
    "tesseract": TesseractOCR,
    "docling": DoclingOCR,
    "docling_layout": DoclingLayoutOCR,
}


def benchmark(provider, sub_bytes: bytes, sample: int, threshold: float) -> dict:
    t0 = time.perf_counter()
    pages = provider.process(sub_bytes, "application/pdf")
    elapsed_total = time.perf_counter() - t0

    confidences = [p.confidence for p in pages]
    folios = [detect_folio(p.text) for p in pages]
    low_conf = sum(1 for c in confidences if c < threshold)

    # Métricas de layout: líneas con estructura clave-valor detectada.
    kv_lines = sum(1 for p in pages for line in p.text.split("\n") if ":" in line and len(line.split(":")) == 2)
    total_lines = sum(1 for p in pages for line in p.text.split("\n") if line.strip())

    return {
        "sample_pages": len(pages),
        "elapsed_seconds": round(elapsed_total, 2),
        "seconds_per_page_mean": round(elapsed_total / len(pages), 2) if pages else 0,
        "confidence_mean": round(statistics.mean(confidences), 3) if confidences else 0,
        "confidence_median": round(statistics.median(confidences), 3) if confidences else 0,
        "confidence_min": round(min(confidences), 3) if confidences else 0,
        "confidence_max": round(max(confidences), 3) if confidences else 0,
        "under_threshold_count": low_conf,
        "under_threshold_pct": round(100 * low_conf / len(pages), 1) if pages else 0,
        "folios_detected": sum(1 for f in folios if f),
        "kv_lines_detected": kv_lines,
        "total_lines": total_lines,
        "kv_ratio": round(kv_lines / total_lines, 3) if total_lines else 0,
        "pages": [
            {
                "page_number": p.page_number,
                "confidence": round(p.confidence, 3),
                "folio": detect_folio(p.text),
                "text_sample": p.text[:200].replace("\n", " "),
            }
            for p in pages
        ],
    }


def print_report(report: dict, provider_name: str) -> None:
    print(f"\n=== {provider_name} ===")
    print(f"Páginas procesadas: {report['sample_pages']}")
    print(f"Tiempo total:       {report['elapsed_seconds']} s")
    print(f"Tiempo/página:      {report['seconds_per_page_mean']} s")
    print(f"Confianza media:    {report['confidence_mean']}")
    print(f"Confianza mediana:  {report['confidence_median']}")
    print(f"Confianza min/max:  {report['confidence_min']} / {report['confidence_max']}")
    print(f"Bajo umbral:        {report['under_threshold_count']} ({report['under_threshold_pct']}%)")
    print(f"Folios detectados:  {report['folios_detected']}")
    print(f"Líneas clave-valor: {report['kv_lines_detected']} / {report['total_lines']} ({report['kv_ratio']})")
    print("Muestras de texto:")
    for p in report["pages"][:3]:
        print(f"  p{p['page_number']:03d} conf={p['confidence']} folio={p['folio']}: {p['text_sample'][:120]}")


def compare_providers(sub_bytes: bytes, sample: int, threshold: float) -> dict:
    """Ejecuta todos los proveedores y devuelve comparativa."""
    results = {}
    for name, cls in PROVIDER_CLASSES.items():
        print(f"\nEjecutando {name}...")
        try:
            provider = cls()
            report = benchmark(provider, sub_bytes, sample, threshold)
            report["provider"] = name
            results[name] = report
        except Exception as exc:
            print(f"  ERROR en {name}: {exc}")
            results[name] = {"error": str(exc)}
    return results


def print_comparison(results: dict) -> None:
    print("\n" + "=" * 60)
    print("COMPARATIVA DE PROVEEDORES")
    print("=" * 60)
    headers = ["Proveedor", "Páginas", "Tiempo(s)", "s/página", "Conf. media", "Bajo umbral", "KV ratio"]
    print(f"{headers[0]:<16} {headers[1]:<8} {headers[2]:<10} {headers[3]:<10} {headers[4]:<12} {headers[5]:<12} {headers[6]:<10}")
    print("-" * 80)
    for name, r in results.items():
        if "error" in r:
            print(f"{name:<16} ERROR: {r['error'][:50]}")
        else:
            print(f"{name:<16} {r['sample_pages']:<8} {r['elapsed_seconds']:<10} {r['seconds_per_page_mean']:<10} {r['confidence_mean']:<12} {r['under_threshold_count']:<12} {r['kv_ratio']:<10}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark OCR comparativo")
    ap.add_argument("--provider", choices=["tesseract", "docling", "docling_layout"], default="tesseract")
    ap.add_argument("--compare-all", action="store_true", help="Compara tesseract, docling y docling_layout")
    ap.add_argument("--sample", type=int, default=int(os.environ.get("OCR_BENCHMARK_SAMPLE", DEFAULT_SAMPLE)))
    args = ap.parse_args()

    s = get_settings()
    if not CUADERNO_PRINCIPAL.exists():
        print(f"ERROR: no existe {CUADERNO_PRINCIPAL}")
        sys.exit(1)

    with fitz.open(CUADERNO_PRINCIPAL) as doc:
        total_pages = doc.page_count
    sample = min(args.sample, total_pages)

    print(f"Benchmark OCR: {CUADERNO_PRINCIPAL.name}")
    print(f"  Páginas totales:   {total_pages}")
    print(f"  Muestra:           1-{sample} ({sample} páginas)")
    print(f"  DPI:               {s.OCR_DPI}")
    print(f"  Preprocesamiento:  {s.OCR_PREPROCESS}")
    print("Renderizando sub-PDF de muestra...")

    sub_bytes = build_sub_pdf(CUADERNO_PRINCIPAL, 0, sample)

    if args.compare_all:
        results = compare_providers(sub_bytes, sample, s.OCR_CONFIDENCE_THRESHOLD)
        for name, r in results.items():
            if "error" not in r:
                print_report(r, name)
        print_comparison(results)

        COMPARE_JSON.parent.mkdir(parents=True, exist_ok=True)
        COMPARE_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nComparativa guardada en: {COMPARE_JSON}")
    else:
        provider = PROVIDER_CLASSES[args.provider]()
        report = benchmark(provider, sub_bytes, sample, s.OCR_CONFIDENCE_THRESHOLD)
        report.update({
            "document": str(CUADERNO_PRINCIPAL),
            "total_pages_in_document": total_pages,
            "ocr_provider": args.provider,
            "dpi": s.OCR_DPI,
            "preprocess": s.OCR_PREPROCESS,
        })
        print_report(report, args.provider)

        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReporte guardado en: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
