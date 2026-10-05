"""Proveedores OCR (SSD §137-138). Implementación baseline: Tesseract local.

El protocolo `OCRProvider` vive en `app.providers.base`. Cada proveedor recibe los
bytes del documento y su MIME, y devuelve una lista de páginas con texto, confianza
y layout mínimo (bounding boxes por palabra).
"""
from __future__ import annotations

import io
import logging
import re
import shlex
from dataclasses import dataclass
from typing import Any

import numpy as np
import pymupdf as fitz
import pytesseract
from PIL import Image

from app.core.config import get_settings
from app.services import image_preprocessing
from app.services.handwriting_ocr import get_line_recognizer, refine_lines
from app.services.ocr_postprocess import normalize_ocr_text

log = logging.getLogger(__name__)

# Presupuesto máximo de píxeles por página rasterizada. Escaneos gigantes (planos,
# mapas) a 300 DPI pueden superar los 150 MP y agotar la RAM del contenedor (el
# worker muere por OOM/SIGKILL). Si se excede, se baja el DPI automáticamente.
_MAX_RENDER_PIXELS = 40_000_000


def _render_page_bytes(page: "fitz.Page", dpi: int) -> bytes:
    """Renderiza la página a PNG, bajando el DPI si excede el presupuesto de píxeles."""
    rect = page.rect
    est = (rect.width / 72.0 * dpi) * (rect.height / 72.0 * dpi)
    if est > _MAX_RENDER_PIXELS:
        dpi = max(72, int(dpi * (_MAX_RENDER_PIXELS / est) ** 0.5))
        log.info("Página grande (%.1f MP a %s DPI): bajando a %s DPI", est / 1e6, dpi, dpi)
    return page.get_pixmap(dpi=dpi).tobytes("png")


@dataclass
class OCRPage:
    """Resultado OCR de una página."""

    page_number: int
    text: str
    confidence: float
    words: list[dict[str, Any]]


def _mean_confidence(words: list[dict[str, Any]]) -> float:
    """Promedia confianzas válidas (>0); devuelve 0.0 si no hay palabras."""
    vals = [w["confidence"] for w in words if w.get("confidence", -1) > 0]
    return sum(vals) / len(vals) if vals else 0.0


def _clean_tesseract_text(text: str) -> str:
    """Normaliza el texto OCR conservando la estructura visual: saltos de línea,
    separaciones de párrafo (máx. una línea en blanco) y huecos horizontales."""
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)     # espacios al final de línea
    text = re.sub(r"\t", " ", text)
    text = re.sub(r" {9,}", "        ", text)  # huecos enormes => 8 espacios
    text = re.sub(r"\n{3,}", "\n\n", text)     # máximo una línea en blanco seguida
    return text.strip()


def _layout_text(words: list[dict[str, Any]]) -> str:
    """Reconstruye el texto respetando la geometría de la página.

    Tesseract/RapidOCR emiten las palabras en orden de detección, no en orden de
    lectura, y un `join` plano deja todo en un solo párrafo (las etiquetas quedan
    tras sus valores, p. ej. "Civil del circuito Bogotá URISDICCIÓN:"). Aquí se
    agrupan las palabras en líneas por su coordenada Y, se ordenan de izquierda a
    derecha dentro de cada línea, y se restauran los saltos de línea y las
    separaciones de párrafo según los huecos verticales/horizontales detectados.

    Mejoras para formularios judiciales:
    - Detecta pares etiqueta–valor en la misma línea (hueco horizontal grande).
    - Reconoce encabezados de sección (texto en mayúsculas o centrado).
    - Conserva la estructura de columnas sin colapsar el contenido.
    """
    items: list[dict[str, Any]] = []
    for w in words:
        b = w.get("bbox") or {}
        if "points" in b:  # RapidOCR: polígono de la línea detectada
            pts = b["points"]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x, y = min(xs), min(ys)
            bw, bh = max(xs) - x, max(ys) - y
        else:  # Tesseract: caja x/y/w/h por palabra
            x, y = b.get("x", 0), b.get("y", 0)
            bw, bh = b.get("w", 0), b.get("h", 0)
        items.append({"text": w["text"], "x": float(x), "y": float(y), "w": float(bw), "h": float(bh)})
    if not items:
        return ""

    heights = sorted(i["h"] for i in items if i["h"] > 0)
    med_h = heights[len(heights) // 2] if heights else 12.0
    line_tol = max(4.0, med_h * 0.7)

    # Agrupa en líneas por proximidad vertical (recorrido de arriba abajo).
    lines: list[dict[str, Any]] = []
    for it in sorted(items, key=lambda i: (i["y"], i["x"])):
        if lines and abs(it["y"] - lines[-1]["y"]) <= line_tol:
            lines[-1]["items"].append(it)
            lines[-1]["bottom"] = max(lines[-1]["bottom"], it["y"] + it["h"])
        else:
            lines.append({"y": it["y"], "bottom": it["y"] + it["h"], "items": [it]})

    # (Se eliminó el cálculo del ancho típico de página: no se usaba.)

    out: list[str] = []
    prev_bottom: float | None = None
    for line in lines:
        # Hueco vertical grande => separación de párrafo/sección.
        if prev_bottom is not None and line["y"] - prev_bottom > med_h * 1.6:
            out.append("")
        parts: list[str] = []
        prev_right: float | None = None
        line_items = sorted(line["items"], key=lambda i: i["x"])
        for it in line_items:
            if prev_right is not None:
                gap = it["x"] - prev_right
                # Hueco horizontal grande => separación visual de columnas/campos.
                if gap > med_h * 2.5:
                    parts.append(" " * min(12, max(4, round(gap / (med_h * 0.5)))))
                elif gap > med_h:
                    parts.append(" " * min(8, max(2, round(gap / (med_h * 0.6)))))
                else:
                    parts.append(" ")
            parts.append(it["text"])
            prev_right = max(prev_right or 0.0, it["x"] + it["w"])
        line_text = "".join(parts).rstrip()

        # Heurística de formulario: si hay un hueco horizontal grande, intenta
        # formatear como "etiqueta: valor" cuando la parte izquierda parece
        # una etiqueta (termina en ':' o es corta y descriptiva).
        if len(line_items) >= 2:
            gaps = []
            prev = None
            for it in line_items:
                if prev is not None:
                    gaps.append(it["x"] - (prev["x"] + prev["w"]))
                prev = it
            if gaps and max(gaps) > med_h * 3.0:
                split_idx = gaps.index(max(gaps))
                left = " ".join(i["text"] for i in line_items[: split_idx + 1])
                right = " ".join(i["text"] for i in line_items[split_idx + 1 :])
                left_clean = left.rstrip()
                # Etiqueta corta (<= 40 chars) que termina en ':' o es un campo conocido.
                if len(left_clean) <= 40 and (left_clean.endswith(":") or _looks_like_label(left_clean)):
                    line_text = f"{left_clean} {right}"

        out.append(line_text)
        prev_bottom = line["bottom"]
    return "\n".join(out)


# Campos típicos de formularios judiciales colombianos que suelen actuar como etiquetas.
_FORM_LABELS = {
    "jurisdicción", "grupo/clase", "grupo", "clase", "no.", "número", "cuadernos",
    "folios", "traslados", "demandante", "demandado", "apoderado", "nombre",
    "nombres", "apellido", "dirección", "notificación", "teléfono", "c.c", "nit",
    "anexos", "radicación", "juzgado", "expediente", "proceso", "fecha",
}


def _looks_like_label(text: str) -> bool:
    """Heurística: ¿el texto parece una etiqueta de formulario?"""
    t = text.strip().lower()
    if not t or len(t) > 40:
        return False
    if t.endswith(":"):
        return True
    if t in _FORM_LABELS:
        return True
    # Palabra corta en mayúsculas (p. ej. "DEMANDANTE(S)").
    if len(t.split()) <= 3 and text.isupper():
        return True
    return False


def _page_has_text(page: fitz.Page) -> bool:
    """True si la página ya tiene capa de texto extraíble."""
    return bool(page.get_text().strip())


class TesseractOCR:
    """OCR local vía Tesseract + PyMuPDF. No envía datos a la nube."""

    name = "tesseract"

    def __init__(self) -> None:
        s = get_settings()
        self.lang = s.OCR_TESSERACT_LANG
        self.dpi = s.OCR_DPI
        self.tessdata_dir = s.OCR_TESSERACT_TESSDATA_DIR or ""
        self.preprocess = s.OCR_PREPROCESS
        if s.OCR_TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = s.OCR_TESSERACT_CMD

    def process(self, document_bytes: bytes, mime_type: str,
                progress_cb: Any | None = None) -> list[OCRPage]:
        if not document_bytes:
            raise ValueError("document_bytes is empty")
        # Abrimos con PyMuPDF; soporta PDF e imágenes.
        doc = fitz.open(stream=document_bytes, filetype="pdf" if mime_type == "application/pdf" else "png")
        try:
            pages: list[OCRPage] = []
            for idx in range(doc.page_count):
                page = doc.load_page(idx)
                page_number = idx + 1
                if _page_has_text(page):
                    # sort=True: orden de lectura (de arriba abajo, izquierda a derecha).
                    text = normalize_ocr_text(_clean_tesseract_text(page.get_text("text", sort=True)))
                    pages.append(OCRPage(page_number=page_number, text=text, confidence=1.0, words=[]))
                    continue
                image = Image.open(io.BytesIO(_render_page_bytes(page, self.dpi)))
                if self.preprocess:
                    image = image_preprocessing.preprocess(image)
                config = f"--tessdata-dir {shlex.quote(self.tessdata_dir)}" if self.tessdata_dir else None
                data = pytesseract.image_to_data(
                    image, lang=self.lang, config=config, output_type=pytesseract.Output.DICT
                )
                words = []
                for i, word in enumerate(data["text"]):
                    conf = int(data["conf"][i])
                    if not word.strip() or conf <= 0:
                        continue
                    words.append({
                        "text": word,
                        "confidence": conf / 100.0,
                        "bbox": {
                            "x": data["left"][i],
                            "y": data["top"][i],
                            "w": data["width"][i],
                            "h": data["height"][i],
                        },
                    })
                # Respeta el orden espacial del documento (líneas y párrafos).
                text = normalize_ocr_text(_clean_tesseract_text(_layout_text(words)))
                confidence = _mean_confidence(words)
                pages.append(OCRPage(page_number=page_number, text=text, confidence=confidence, words=words))
            return pages
        finally:
            doc.close()


class FakeOCR:
    """Proveedor dummy para tests/desarrollo sin Tesseract instalado."""

    name = "fake"

    def process(self, document_bytes: bytes, mime_type: str,
                progress_cb: Any | None = None) -> list[OCRPage]:
        # Simula un documento de 2 páginas para que los tests tengan datos clasificables.
        return [
            OCRPage(page_number=1, text="Demanda principal del proceso.", confidence=0.95, words=[]),
            OCRPage(page_number=2, text="Anexos de la demanda.", confidence=0.95, words=[]),
        ]


class DoclingOCR:
    """OCR vía Docling/RapidOCR (ONNX, local). Fallback a Tesseract si RapidOCR falla."""

    name = "docling"

    def __init__(self) -> None:
        s = get_settings()
        self.dpi = s.OCR_DPI
        self.preprocess = s.OCR_PREPROCESS
        self.engine = self._build_engine()
        self._fallback = TesseractOCR()

    def _build_engine(self):
        """Crea el motor RapidOCR. Las subclases eligen modelo/idioma de reconocimiento."""
        from rapidocr import RapidOCR

        return RapidOCR()

    def process(self, document_bytes: bytes, mime_type: str,
                progress_cb: Any | None = None) -> list[OCRPage]:
        if not document_bytes:
            raise ValueError("document_bytes is empty")
        try:
            return self._process_with_rapidocr(document_bytes, mime_type, progress_cb)
        except Exception as exc:
            log.warning("RapidOCR falló (%s); usando Tesseract como fallback", exc)
            return self._fallback.process(document_bytes, mime_type)

    def _process_with_rapidocr(self, document_bytes: bytes, mime_type: str,
                               progress_cb: Any | None = None) -> list[OCRPage]:
        doc = fitz.open(stream=document_bytes, filetype="pdf" if mime_type == "application/pdf" else "png")
        try:
            pages: list[OCRPage] = []
            total = doc.page_count
            for idx in range(total):
                page = doc.load_page(idx)
                page_number = idx + 1
                image = Image.open(io.BytesIO(_render_page_bytes(page, self.dpi)))
                if self.preprocess:
                    image = image_preprocessing.preprocess(image)
                array = np.array(image)
                output = self.engine(array)
                if output is None or output.boxes is None:
                    pages.append(OCRPage(page_number=page_number, text="", confidence=0.0, words=[]))
                else:
                    texts, scores = self._recognize_lines(array, output)
                    words = []
                    for box, text, score in zip(output.boxes, texts, scores, strict=True):
                        if not text.strip():
                            continue
                        # RapidOCR devuelve puntos como ndarray; los convertimos a listas nativas.
                        points = box.tolist() if hasattr(box, "tolist") else box
                        words.append({
                            "text": text,
                            "confidence": float(score),
                            "bbox": {"points": points},
                        })
                    text = normalize_ocr_text(_clean_tesseract_text(_layout_text(words)))
                    confidence = sum(scores) / len(scores) if scores else 0.0
                    pages.append(OCRPage(page_number=page_number, text=text, confidence=confidence, words=words))
                # Progreso en vivo (página a página).
                if progress_cb:
                    try:
                        progress_cb(page_number, total, f"página {page_number}/{total}")
                    except Exception:  # noqa: BLE001
                        log.debug("callback de progreso falló", exc_info=True)
            return pages
        finally:
            doc.close()

    def _recognize_lines(self, image: np.ndarray, output: Any) -> tuple[list[str], list[float]]:
        """Reconoce las líneas detectadas. Las subclases pueden añadir una segunda pasada."""
        return list(output.txts), [float(s) for s in output.scores]


class DoclingLatinOCR(DoclingOCR):
    """RapidOCR con modelo de reconocimiento **latino** (PP-OCRv5 latin).

    Mejora el texto impreso en español (tildes, ñ, mayúsculas) frente al modelo
    multilingüe por defecto, porque su diccionario de salida es exclusivamente latino.
    """

    name = "docling_latin"

    def _build_engine(self):
        from rapidocr import ModelType, OCRVersion, RapidOCR

        return RapidOCR(params={
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.lang_type": "latin",
            "Rec.model_type": ModelType.MOBILE,
        })


class _TokenExpired(Exception):
    """El access token de Google expiró (HTTP 401): hay que renovarlo y reintentar."""


class DocumentAiOCR:
    """OCR con Google Document AI (procesador Document OCR) vía REST API.

    Usa `google-auth` para obtener el access token de la service account y llama
    al endpoint `:process` con el documento en base64. Límite síncrono de la API:
    15 páginas / 20 MB por petición, así que los PDFs más largos se dividen en
    chunks de 15 páginas y los resultados se concatenan.

    Configuración requerida (`.env`):
    - GOOGLE_CLOUD_PROJECT (ID numérico del proyecto, p. ej. 883482796451)
    - GOOGLE_CLOUD_LOCATION (us | eu)
    - GOOGLE_DOCUMENT_AI_PROCESSOR_ID
    - GOOGLE_APPLICATION_CREDENTIALS (ruta al JSON de service account)

    Si falta algo o la llamada falla, cae a Docling (degradación elegante).
    """
    name = "document_ai"
    _MAX_PAGES_PER_CALL = 15  # límite del endpoint síncrono :process
    _MAX_RETRIES = 3          # reintentos por tanda (fallos transitorios de la API)

    def __init__(self) -> None:
        s = get_settings()
        self.project = s.GOOGLE_CLOUD_PROJECT
        self.location = s.GOOGLE_CLOUD_LOCATION
        self.processor_id = s.GOOGLE_DOCUMENT_AI_PROCESSOR_ID
        self.credentials_path = s.GOOGLE_APPLICATION_CREDENTIALS
        self.scope = s.GOOGLE_CLOUD_SCOPE
        self.endpoint_template = s.GOOGLE_DOCUMENT_AI_ENDPOINT_TEMPLATE
        self.dpi = s.OCR_DPI
        self._fallback = DoclingLayoutOCR()  # Docling en lugar de Tesseract (no requiere binario externo)
        # Motor realmente usado en la última llamada: "document_ai" o "basico" (fallback).
        # El pipeline lo usa para etiquetar la versión guardada sin mentir.
        self.last_engine = "document_ai"
        self._token: str | None = None

    @property
    def configured(self) -> bool:
        import os
        creds_ok = bool(self.credentials_path) and os.path.exists(self.credentials_path)
        return bool(self.project and self.processor_id and creds_ok)

    def process(self, document_bytes: bytes, mime_type: str,
                progress_cb: Any | None = None) -> list[OCRPage]:
        if not document_bytes:
            raise ValueError("document_bytes is empty")
        if not self.configured:
            log.warning("Document AI no configurado (revisa GOOGLE_* y el JSON de credenciales); fallback a Docling")
            self.last_engine = "basico"
            return self._fallback.process(document_bytes, mime_type)
        try:
            pages = self._process_chunks(document_bytes, mime_type, progress_cb)
            self.last_engine = "document_ai"
            return pages
        except Exception as exc:
            log.warning("Document AI falló (%s); usando Docling como fallback", exc)
            self.last_engine = "basico"
            return self._fallback.process(document_bytes, mime_type)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _access_token(self) -> str:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GoogleAuthRequest

        creds = service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=[self.scope]
        )
        creds.refresh(GoogleAuthRequest())
        return creds.token

    def _endpoint(self) -> str:
        return self.endpoint_template.format(
            location=self.location, project=self.project, processor=self.processor_id
        )

    def _call_api(self, token: str, pdf_bytes: bytes) -> dict:
        """Una llamada síncrona a :process con un PDF (<=15 páginas)."""
        import base64

        import httpx

        payload = {
            "skipHumanReview": True,
            "rawDocument": {
                "mimeType": "application/pdf",
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
            },
        }
        with httpx.Client(timeout=120.0) as client:
            r = client.post(
                self._endpoint(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=payload,
            )
        if r.status_code == 401:
            raise _TokenExpired()
        if r.status_code != 200:
            raise RuntimeError(f"Document AI HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    def _call_api_robust(self, pdf_bytes: bytes, chunk_label: str) -> dict:
        """Llama a la API con reintentos y renovación de token si expira."""
        import time as _time

        last_exc: Exception | None = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            token = self._token
            try:
                return self._call_api(token, pdf_bytes)
            except _TokenExpired:
                log.warning("Document AI: token expirado en %s; renovando", chunk_label)
                self._token = self._access_token()
                last_exc = _TokenExpired()
            except Exception as exc:  # noqa: BLE001 — reintentamos transitorios (5xx, red)
                last_exc = exc
                log.warning("Document AI: intento %d/%d falló en %s: %s",
                            attempt, self._MAX_RETRIES, chunk_label, str(exc)[:200])
            if attempt < self._MAX_RETRIES:
                _time.sleep(min(2 ** attempt, 8))  # backoff 2s, 4s, 8s
        raise RuntimeError(f"Document AI falló tras {self._MAX_RETRIES} intentos en {chunk_label}: {last_exc}")

    @classmethod
    def _chunk_ranges(cls, total: int) -> list[tuple[int, int]]:
        """Divide `total` páginas en rangos 0-based de a 15: [(0,15), (15,30), ...].

        Para 154 páginas → 11 tandas: 10 de 15 + 1 de 4.
        """
        return [
            (start, min(start + cls._MAX_PAGES_PER_CALL, total))
            for start in range(0, total, cls._MAX_PAGES_PER_CALL)
        ]

    def _process_chunks(self, document_bytes: bytes, mime_type: str,
                        progress_cb: Any | None = None) -> list[OCRPage]:
        """Divide el PDF en chunks de 15 páginas y concatena los resultados en orden."""
        self._token = self._access_token()

        if mime_type != "application/pdf":
            # Imágenes: una sola llamada (el endpoint acepta PNG/JPEG/TIFF).
            raw = self._call_api_robust(document_bytes, "imagen")
            if progress_cb:
                progress_cb(1, 1, "imagen")
            return self._parse_document(raw, page_offset=0)

        with fitz.open(stream=document_bytes, filetype="pdf") as src:
            total = src.page_count
            chunks = self._chunk_ranges(total)
            log.info("Document AI: %d página(s) en %d tanda(s) de %d",
                     total, len(chunks), self._MAX_PAGES_PER_CALL)
            pages: list[OCRPage] = []
            for idx, (start, end) in enumerate(chunks, 1):
                with fitz.open() as chunk:
                    chunk.insert_pdf(src, from_page=start, to_page=end - 1)
                    chunk_bytes = chunk.tobytes()
                label = f"tanda {idx}/{len(chunks)} (páginas {start + 1}-{end})"
                log.info("Document AI: procesando %s", label)
                raw = self._call_api_robust(chunk_bytes, label)
                pages.extend(self._parse_document(raw, page_offset=start))
                if progress_cb:
                    try:
                        progress_cb(end, total, label)
                    except Exception:  # noqa: BLE001 — el progreso nunca debe tumbar el OCR
                        log.debug("callback de progreso falló", exc_info=True)
            return pages

    @staticmethod
    def _page_native_text(full_text: str, page: dict) -> str:
        """Texto de la página en el orden de lectura NATIVO de Document AI.

        Document AI ya devuelve `document.text` en orden de lectura correcto
        (párrafos, columnas y formularios resueltos). Reconstruirlo con heurísticas
        geométricas propias lo desordena: por eso usamos el textAnchor de la página.
        """
        anchor = page.get("layout", {}).get("textAnchor", {})
        segments = anchor.get("textSegments", [])
        if not segments:
            return ""
        parts: list[str] = []
        for seg in segments:
            s = int(seg.get("startIndex", 0) or 0)
            e = int(seg.get("endIndex", 0) or 0)
            parts.append(full_text[s:e])
        return "".join(parts)

    def _parse_document(self, raw: dict, page_offset: int) -> list[OCRPage]:
        """Convierte la respuesta JSON de Document AI en OCRPage.

        Document AI numera las páginas del *chunk* desde 1; `page_offset` es el
        índice 0-based de la primera página del chunk dentro del PDF original,
        así la numeración global se conserva (chunk 2, página 1 → página 16).
        """
        document = raw.get("document", {})
        full_text = document.get("text", "") or ""
        pages: list[OCRPage] = []

        for idx, page in enumerate(document.get("pages", [])):
            # pageNumber es 1-based dentro del chunk; si falta, usamos el índice.
            chunk_page = int(page.get("pageNumber", 0)) or (idx + 1)
            page_number = chunk_page + page_offset
            dim = page.get("dimension", {})
            width = float(dim.get("width", 1)) or 1.0
            height = float(dim.get("height", 1)) or 1.0

            words: list[dict[str, Any]] = []
            for token in page.get("tokens", []):
                layout = token.get("layout", {})
                anchor = layout.get("textAnchor", {})
                segments = anchor.get("textSegments", [])
                if not segments:
                    continue
                seg = segments[0]
                start = int(seg.get("startIndex", 0) or 0)
                end = int(seg.get("endIndex", 0) or 0)
                text = full_text[start:end].strip()
                if not text:
                    continue

                bbox: dict[str, float] = {}
                poly = layout.get("boundingPoly", {})
                vertices = poly.get("normalizedVertices") or poly.get("vertices") or []
                if vertices:
                    xs = [float(v.get("x", 0)) for v in vertices]
                    ys = [float(v.get("y", 0)) for v in vertices]
                    # normalizedVertices => 0..1; vertices => píxeles.
                    if poly.get("normalizedVertices"):
                        xs = [x * width for x in xs]
                        ys = [y * height for y in ys]
                    bbox = {
                        "x": min(xs), "y": min(ys),
                        "w": max(xs) - min(xs), "h": max(ys) - min(ys),
                    }

                words.append({
                    "text": text,
                    "confidence": float(layout.get("confidence", 0.0)),
                    "bbox": bbox,
                })

            # Orden de lectura NATIVO de Document AI (no re-layout propio).
            text = normalize_ocr_text(self._page_native_text(full_text, page))
            confidence = sum(w["confidence"] for w in words) / len(words) if words else 0.0
            pages.append(OCRPage(page_number=page_number, text=text,
                                 confidence=confidence, words=words))
        return pages


class DoclingLayoutOCR:
    """OCR con análisis de layout real usando Docling DocumentConverter.

    A diferencia de `DoclingOCR` (que sólo usa RapidOCR), este proveedor invoca
    el pipeline completo de Docling: OCR + análisis de estructura + exportación
    a markdown conservando tablas, encabezados y pares clave-valor.
    """
    name = "docling_layout"

    def __init__(self) -> None:
        s = get_settings()
        self.dpi = s.OCR_DPI
        self.preprocess = s.OCR_PREPROCESS
        self._converter = self._build_converter()
        self._fallback = TesseractOCR()

    def _build_converter(self):
        """Construye el DocumentConverter de Docling con OCR RapidOCR."""
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        pipeline_options = PdfPipelineOptions(
            do_ocr=True,
            ocr_options=RapidOcrOptions(),
            do_table_structure=True,
        )
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )

    def process(self, document_bytes: bytes, mime_type: str,
                progress_cb: Any | None = None) -> list[OCRPage]:
        if not document_bytes:
            raise ValueError("document_bytes is empty")
        try:
            return self._process_with_docling(document_bytes, mime_type)
        except Exception as exc:
            log.warning("DoclingLayout falló (%s); usando Tesseract como fallback", exc)
            return self._fallback.process(document_bytes, mime_type)

    def _process_with_docling(self, document_bytes: bytes, mime_type: str) -> list[OCRPage]:
        """Convierte con Docling y extrae texto estructurado por página."""
        import tempfile
        from pathlib import Path

        # Docling requiere un archivo físico, no bytes.
        suffix = ".pdf" if mime_type == "application/pdf" else ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(document_bytes)
            tmp_path = Path(tmp.name)

        try:
            result = self._converter.convert(str(tmp_path))
            doc = result.document

            # Agrupa el contenido por página usando los metadatos de Docling.
            pages: list[OCRPage] = []
            page_items: dict[int, list[dict[str, Any]]] = {}

            for item, _level in doc.iterate_items():
                # Obtiene la página del item (prov.prov_no o metadata).
                page_no = 1
                if hasattr(item, "prov") and item.prov:
                    prov = item.prov[0] if isinstance(item.prov, list) else item.prov
                    page_no = getattr(prov, "page_no", 1)
                text = getattr(item, "text", "") or ""
                if not text.strip():
                    continue
                bbox = None
                if hasattr(item, "prov") and item.prov:
                    prov = item.prov[0] if isinstance(item.prov, list) else item.prov
                    bbox = getattr(prov, "bbox", None)
                page_items.setdefault(page_no, []).append({
                    "text": text.strip(),
                    "bbox": bbox,
                    "type": type(item).__name__,
                })

            for page_no in sorted(page_items.keys()):
                items = page_items[page_no]
                words = []
                for it in items:
                    bbox = it["bbox"]
                    # Convertir BoundingBox de Docling a dict plano para JSON.
                    if bbox and hasattr(bbox, "__dict__"):
                        bbox = {k: v for k, v in bbox.__dict__.items() if not k.startswith("_")}
                    if bbox:
                        words.append({
                            "text": it["text"],
                            "confidence": 1.0,
                            "bbox": bbox,
                        })
                    else:
                        words.append({"text": it["text"], "confidence": 1.0, "bbox": {}})

                # Construye texto preservando la estructura detectada por Docling.
                text_parts = []
                for it in items:
                    text_parts.append(it["text"])
                text = "\n".join(text_parts)
                confidence = 1.0
                pages.append(OCRPage(page_number=page_no, text=text, confidence=confidence, words=words))

            return pages
        finally:
            tmp_path.unlink(missing_ok=True)


class HandwritingOCR(DoclingOCR):
    """RapidOCR (impreso) + segunda pasada de manuscrito con un modelo image-to-text.

    Sólo se activa si `OCR_HANDWRITING_MODEL` apunta a un modelo cargable; si no,
    se comporta exactamente como `DoclingOCR` (degradación elegante).
    """

    name = "handwriting"

    def __init__(self) -> None:
        super().__init__()
        self.recognizer = get_line_recognizer(get_settings().OCR_HANDWRITING_MODEL)

    def _recognize_lines(self, image: np.ndarray, output: Any) -> tuple[list[str], list[float]]:
        threshold = get_settings().OCR_CONFIDENCE_THRESHOLD
        return refine_lines(
            image=image,
            boxes=list(output.boxes),
            texts=list(output.txts),
            scores=[float(s) for s in output.scores],
            recognizer=self.recognizer,
            threshold=threshold,
        )


# Registro proveedor -> implementación (SSD §137: el dominio no depende del motor concreto).
OCR_PROVIDER_CLASSES: dict[str, type] = {
    "tesseract": TesseractOCR,
    "docling": DoclingOCR,
    "docling_layout": DoclingLayoutOCR,
    "docling_latin": DoclingLatinOCR,
    "handwriting": HandwritingOCR,
    "document_ai": DocumentAiOCR,
    "fake": FakeOCR,
}


def get_ocr_provider() -> TesseractOCR | DoclingOCR | FakeOCR:
    s = get_settings()
    return OCR_PROVIDER_CLASSES.get(s.OCR_PROVIDER, FakeOCR)()
