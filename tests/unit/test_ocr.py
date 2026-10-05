"""UT-OCR — proveedores OCR (Fase 2)."""
from __future__ import annotations

import pytest

from app.providers.ocr import (
    DoclingLatinOCR,
    DoclingLayoutOCR,
    FakeOCR,
    OCR_PROVIDER_CLASSES,
    TesseractOCR,
    _clean_tesseract_text,
    _layout_text,
    get_ocr_provider,
)

pytestmark = pytest.mark.unit


def test_ut_ocr_01_fake_ocr_returns_pages():
    provider = FakeOCR()
    pages = provider.process(b"fake pdf bytes", "application/pdf")
    assert len(pages) == 2
    assert pages[0].page_number == 1
    assert pages[0].text
    assert 0 <= pages[0].confidence <= 1


def test_ut_ocr_02_get_provider_is_fake_in_tests():
    provider = get_ocr_provider()
    assert provider.name == "fake"


def test_ut_ocr_03_tesseract_provider_class_exists():
    # No asume que tesseract esté instalado; solo verifica la clase.
    assert TesseractOCR.name == "tesseract"


def _w(text: str, x: float, y: float, w: float = 40, h: float = 12) -> dict:
    return {"text": text, "confidence": 0.9, "bbox": {"x": x, "y": y, "w": w, "h": h}}


def test_ut_ocr_04_layout_text_respeta_orden_espacial():
    # Formulario: la etiqueta está a la izquierda del valor en la misma línea,
    # pero el motor OCR emitió primero el valor manuscrito (orden de detección).
    words = [
        _w("Bogotá", x=300, y=100),
        _w("URISDICCIÓN:", x=10, y=100),
        _w("del", x=220, y=101),
        _w("circuito", x=250, y=99),
        _w("Civil", x=180, y=100),
        _w("DEMANDANTE(S)", x=10, y=130),
        _w("RUEDA", x=200, y=130),
    ]
    text = _layout_text(words)
    lines = text.split("\n")
    assert len(lines) == 2
    # La etiqueta precede al valor en la misma línea.
    assert lines[0].startswith("URISDICCIÓN:")
    assert lines[0].index("URISDICCIÓN:") < lines[0].index("Civil del circuito Bogotá")
    assert lines[1].startswith("DEMANDANTE(S)")


def test_ut_ocr_05_layout_text_separa_parrafos():
    words = [
        _w("Título", x=10, y=10),
        _w("Párrafo", x=10, y=60),   # hueco vertical grande => línea en blanco
        _w("siguiente", x=10, y=75),
    ]
    text = _layout_text(words)
    assert text == "Título\n\nPárrafo\nsiguiente"


def test_ut_ocr_06_layout_text_cajas_rapidocr():
    words = [
        {"text": "línea dos", "confidence": 0.9, "bbox": {"points": [[10, 30], [100, 30], [100, 42], [10, 42]]}},
        {"text": "línea uno", "confidence": 0.9, "bbox": {"points": [[10, 10], [100, 10], [100, 22], [10, 22]]}},
    ]
    assert _layout_text(words) == "línea uno\nlínea dos"


def test_ut_ocr_07_clean_conserva_saltos_y_colapsa_exceso():
    assert _clean_tesseract_text("a\n\n\n\nb") == "a\n\nb"
    assert _clean_tesseract_text("a  \nb") == "a\nb"
    assert _clean_tesseract_text("a" + " " * 20 + "b") == "a" + " " * 8 + "b"


def test_ut_ocr_08_docling_latin_es_proveedor_de_reconocimiento_latino():
    """El proveedor docling_latin usa el modelo latino sin cambiar el resto del pipeline."""
    assert DoclingLatinOCR.name == "docling_latin"
    assert issubclass(DoclingLatinOCR, TesseractOCR) is False
    # El registro permite elegirlo por configuración sin instanciar el motor (no descarga modelos).
    assert OCR_PROVIDER_CLASSES["docling_latin"] is DoclingLatinOCR
    assert set(OCR_PROVIDER_CLASSES) == {"tesseract", "docling", "docling_layout", "docling_latin", "handwriting", "document_ai", "fake"}


def test_ut_ocr_09_docling_layout_es_proveedor_con_layout_real():
    """El proveedor docling_layout usa el DocumentConverter de Docling para análisis de estructura."""
    assert DoclingLayoutOCR.name == "docling_layout"
    assert OCR_PROVIDER_CLASSES["docling_layout"] is DoclingLayoutOCR
    assert issubclass(DoclingLayoutOCR, TesseractOCR) is False


def test_ut_ocr_10_layout_text_detecta_etiqueta_valor():
    """En formularios, el hueco horizontal grande entre etiqueta y valor se conserva."""
    words = [
        _w("JURISDICCIÓN:", x=10, y=100),
        _w("Civil", x=200, y=100),
        _w("del", x=240, y=100),
        _w("Circuito", x=280, y=100),
        _w("de", x=340, y=100),
        _w("Bogotá", x=370, y=100),
        _w("DEMANDANTE(S)", x=10, y=140),
        _w("José", x=200, y=140),
        _w("Rueda", x=240, y=140),
    ]
    text = _layout_text(words)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    assert len(lines) == 2
    # La etiqueta precede al valor en la misma línea.
    assert lines[0].startswith("JURISDICCIÓN:")
    assert "Civil del Circuito de Bogotá" in lines[0]
    assert lines[1].startswith("DEMANDANTE(S)")
    assert "José Rueda" in lines[1]


def test_ut_ocr_11_layout_text_separa_secciones():
    """Encabezados de sección en mayúsculas se reconocen como líneas propias."""
    words = [
        _w("DEMANDANTE(S)", x=10, y=10),
        _w("Nombre(s)", x=10, y=40),
        _w("José", x=100, y=40),
        _w("DEMANDADO(S)", x=10, y=90),
        _w("Nombre(s)", x=10, y=120),
        _w("Juan", x=100, y=120),
    ]
    text = _layout_text(words)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    assert len(lines) == 4
    assert lines[0] == "DEMANDANTE(S)"
    assert lines[2] == "DEMANDADO(S)"


def _docai_response(page_numbers: list[int], text: str = "Hola mundo") -> dict:
    """Construye una respuesta simulada de Document AI con N páginas."""
    pages = []
    for pn in page_numbers:
        pages.append({
            "pageNumber": pn,
            "dimension": {"width": 100, "height": 200},
            "tokens": [{
                "layout": {
                    "textAnchor": {"textSegments": [{"startIndex": 0, "endIndex": len(text)}]},
                    "boundingPoly": {"normalizedVertices": [
                        {"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1},
                        {"x": 0.5, "y": 0.2}, {"x": 0.1, "y": 0.2},
                    ]},
                    "confidence": 0.95,
                }
            }],
        })
    return {"document": {"text": text, "pages": pages}}


def test_ut_ocr_12_document_ai_parse_preserva_orden_con_offset():
    """El chunking de Document AI numera las páginas globalmente (offset por chunk)."""
    from app.providers.ocr import DocumentAiOCR

    provider = DocumentAiOCR.__new__(DocumentAiOCR)  # sin __init__ (no toca credenciales)

    # Chunk 1: páginas 1-15 del PDF (offset 0).
    chunk1 = provider._parse_document(_docai_response(list(range(1, 16)), "Página uno"), page_offset=0)
    assert [p.page_number for p in chunk1] == list(range(1, 16))

    # Chunk 2: páginas 16-30 del PDF (offset 15). Document AI las numera 1-15.
    chunk2 = provider._parse_document(_docai_response(list(range(1, 16)), "Página dos"), page_offset=15)
    assert [p.page_number for p in chunk2] == list(range(16, 31))

    # Chunk 11 (último de 154 páginas): páginas 151-154 (offset 150), solo 4 páginas.
    chunk11 = provider._parse_document(_docai_response([1, 2, 3, 4], "Final"), page_offset=150)
    assert [p.page_number for p in chunk11] == [151, 152, 153, 154]

    # Concatenado conserva el orden global completo.
    all_pages = chunk1 + chunk2 + chunk11
    numbers = [p.page_number for p in all_pages]
    assert numbers == sorted(numbers)
    assert numbers[0] == 1 and numbers[-1] == 154


def test_ut_ocr_13_document_ai_parse_sin_page_number_usa_indice():
    """Si Document AI omite pageNumber, el parser usa el índice + offset."""
    from app.providers.ocr import DocumentAiOCR

    provider = DocumentAiOCR.__new__(DocumentAiOCR)
    raw = {"document": {"text": "abc", "pages": [
        {"dimension": {"width": 10, "height": 10}, "tokens": []},
        {"dimension": {"width": 10, "height": 10}, "tokens": []},
    ]}}
    pages = provider._parse_document(raw, page_offset=30)
    assert [p.page_number for p in pages] == [31, 32]


def test_ut_ocr_15_document_ai_tandas_154_paginas():
    """154 páginas → 11 tandas de 15 (10 completas + 4 finales), sin huecos ni solapes."""
    from app.providers.ocr import DocumentAiOCR

    chunks = DocumentAiOCR._chunk_ranges(154)
    assert len(chunks) == 11
    assert chunks[0] == (0, 15)
    assert chunks[1] == (15, 30)
    assert chunks[-1] == (150, 154)  # última tanda: 4 páginas (151-154)
    # Cobertura exacta: 0..153 sin huecos ni solapes.
    assert chunks[0][0] == 0
    assert chunks[-1][1] == 154
    for (_, end), (nxt_start, _) in zip(chunks, chunks[1:]):
        assert end == nxt_start


def test_ut_ocr_16_document_ai_tandas_casos_limite():
    """Casos límite: 0, 1, 15 y 16 páginas."""
    from app.providers.ocr import DocumentAiOCR

    assert DocumentAiOCR._chunk_ranges(0) == []
    assert DocumentAiOCR._chunk_ranges(1) == [(0, 1)]
    assert DocumentAiOCR._chunk_ranges(15) == [(0, 15)]
    assert DocumentAiOCR._chunk_ranges(16) == [(0, 15), (15, 16)]


def test_ut_ocr_17_document_ai_usa_orden_nativo_no_relayout():
    """El texto de Document AI se toma en su orden nativo (sin re-layout geométrico).

    Document AI ya resuelve formularios/columnas; re-ordenar con heurísticas propias
    desordena el resultado (p. ej. 'CIVIL DATOS PARA RADICACIÓN CIRCUITO...').
    """
    from app.providers.ocr import DocumentAiOCR

    provider = DocumentAiOCR.__new__(DocumentAiOCR)
    full = "JURISDICCION:Civl Del Circuito De Bogotá\nGrupo/Clase de Proceso: EJECUTIVO SINGULAR"
    page = {
        "pageNumber": 1,
        "dimension": {"width": 100, "height": 200},
        "layout": {"textAnchor": {"textSegments": [{"startIndex": 0, "endIndex": len(full)}]}},
        "tokens": [],
    }
    raw = {"document": {"text": full, "pages": [page]}}
    pages = provider._parse_document(raw, page_offset=0)
    assert len(pages) == 1
    # Se conserva exactamente el orden nativo de Document AI.
    assert pages[0].text == full


def test_ut_ocr_18_page_native_text_concatena_segmentos():
    """`_page_native_text` une todos los segmentos del textAnchor de la página."""
    from app.providers.ocr import DocumentAiOCR

    full = "AAA BBB CCC"
    page = {"layout": {"textAnchor": {"textSegments": [
        {"startIndex": 0, "endIndex": 3},    # "AAA"
        {"startIndex": 3, "endIndex": 7},    # " BBB"
        {"startIndex": 7, "endIndex": 11},   # " CCC"
    ]}}}
    assert DocumentAiOCR._page_native_text(full, page) == "AAA BBB CCC"
    # Página sin layout => cadena vacía (no rompe).
    assert DocumentAiOCR._page_native_text(full, {}) == ""


def test_ut_ocr_14_document_ai_parse_bounding_boxes_normalizados():
    """Los bounding boxes normalizados (0-1) se convierten a píxeles de página."""
    from app.providers.ocr import DocumentAiOCR

    provider = DocumentAiOCR.__new__(DocumentAiOCR)
    pages = provider._parse_document(_docai_response([1], "Hola mundo"), page_offset=0)
    assert len(pages) == 1
    w = pages[0].words[0]
    # Página de 100x200: x=0.1*100=10, y=0.1*200=20, w=0.4*100=40, h=0.1*200=20
    assert w["bbox"] == {"x": 10.0, "y": 20.0, "w": 40.0, "h": 20.0}
    assert w["text"] == "Hola mundo"
    assert w["confidence"] == 0.95
