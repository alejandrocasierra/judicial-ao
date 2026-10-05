"""Pipeline de procesamiento documental: OCR, folio y clasificación (Fase 2).

Este módulo es invocado por el worker `document_ocr`. Mantiene la lógica de
negocio fuera del handler de Celery para facilitar tests unitarios.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

import numpy as np
import pymupdf as fitz
import pytesseract
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.providers.llm import get_llm_for_model
from app.providers.ocr import OCR_PROVIDER_CLASSES, get_ocr_provider
from app.services.document_classifier import classify_document
from app.services.legal_extraction import _check_budget, _load_prompt, _spend_budget
from app.services.storage import key_from_uri, storage


class PipelineError(Exception):
    """Error clasificable dentro del pipeline documental."""

    def __init__(self, error_code: str, detail: str = ""):
        super().__init__(detail or error_code)
        self.error_code = error_code

log = logging.getLogger(__name__)

# Estructuración del OCR con el modelo LLM activo (página por página). Desactivada
# por defecto: añade ~2-3 s por página y su salida ya no se muestra en el visor.
# Actívala con OCR_STRUCTURE_WITH_MODEL=true si vuelves a usar el tab "Campos".
_STRUCTURE_WITH_MODEL = os.environ.get("OCR_STRUCTURE_WITH_MODEL", "false").strip().lower() in ("1", "true", "yes", "on")

# Regexs comunes de foliado en expedientes judiciales colombianos.
FOLIO_PATTERNS = [
    re.compile(r"folio\s*[:°n]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"fol\s*[:°n]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"p[áa]g(?:ina)?\s*[:°n]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"p[áa]g\.\s*[:°n]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"hoja\s*[:°n]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"n[°º]\s*(\d+)\s*(?:folios?|p[áa]ginas?|hojas?)", re.IGNORECASE),
]

# Regex para número aislado (último recurso). Suele ser el folio impreso en
# encabezado/pie; lo buscamos en cualquier línea para no perder folios.
ISOLATED_NUMBER = re.compile(r"^\s*(\d{1,4})\s*$", re.MULTILINE)


def detect_folio(text: str) -> str | None:
    """Busca un número de folio/página en el texto; devuelve el primero o None."""
    for pattern in FOLIO_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    m = ISOLATED_NUMBER.search(text)
    if m:
        return m.group(1)
    return None


def _page_image_key(case_id: str, document_id: str, page_number: int) -> str:
    return f"cases/{case_id}/pages/{document_id}/{page_number:04d}.png"


def _render_page_image(page: fitz.Page, dpi: int) -> bytes:
    pix = page.get_pixmap(dpi=dpi)
    return pix.tobytes("png")


def _to_json_safe(obj: Any) -> Any:
    """Convierte recursivamente tipos no serializables (ndarray, np.float*, BoundingBox de Docling, etc.)."""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(v) for v in obj]
    # Docling BoundingBox y objetos similares: convertir a dict.
    if hasattr(obj, "__dict__"):
        return {k: _to_json_safe(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    return obj


def _serialize_layout(layout: dict[str, Any] | None) -> str | None:
    if not layout:
        return None
    return json.dumps(_to_json_safe(layout), ensure_ascii=False)


def _get_active_ocr_model(conn: Connection) -> dict | None:
    """Modelo LLM marcado como OCR activo en el dashboard (ai_models.ocr_enabled)."""
    return one(conn, "SELECT id, provider, model_name FROM ai_models WHERE ocr_enabled LIMIT 1")


def _structure_ocr_page(
    conn: Connection,
    org_id: str,
    case_id: str,
    user_id: str,
    raw_text: str,
    page_number: int,
) -> dict[str, Any] | None:
    """Usa el modelo OCR activo del dashboard para estructurar el texto en pares clave-valor.

    Si no hay modelo activo, no hay presupuesto o la llamada falla, devuelve None
    y el pipeline continúa con el texto plano (degradación elegante).
    """
    model = _get_active_ocr_model(conn)
    if model is None:
        return None

    estimated = (len(raw_text) + 2000) // 4
    if not _check_budget(conn, case_id, estimated):
        log.info("presupuesto de IA agotado; omitiendo estructuración OCR de página %s", page_number)
        return None

    try:
        body, prompt_id, prompt_version = _load_prompt("structure_ocr", "es", version=2)
        llm = get_llm_for_model(str(model["id"]), org_id, user_id)
        result = llm.complete(body, raw_text)
        _spend_budget(conn, case_id, result.tokens_in, result.tokens_out)

        # Intenta parsear el JSON; si falla, lo guarda como raw_text.
        try:
            structured = json.loads(result.text)
        except json.JSONDecodeError:
            structured = {"raw_text": result.text[:2000], "_parse_error": True}

        one(conn, """INSERT INTO model_runs (organization_id, case_id, task, provider, model,
                        prompt_id, prompt_version, pipeline_version, input_hash, output, tokens_in, tokens_out, actor_id)
                     VALUES (:o,:c,:t,:p,:m,:pid,:pv,'1.0',:ih,CAST(:out AS jsonb),:ti,:to,:a) RETURNING id""",
            o=org_id, c=case_id, t="ocr_structure", p=result.provider, m=result.model,
            pid=prompt_id, pv=prompt_version, ih=hashlib.sha256(raw_text.encode()).hexdigest(),
            out=json.dumps({"page": page_number, "structured": structured}),
            ti=result.tokens_in, to=result.tokens_out, a=user_id)
        return structured
    except Exception as exc:  # noqa: BLE001
        log.warning("estructuración OCR de página %s falló: %s", page_number, exc)
        return None


def _progress_writer(org_id: str, user_id: str, document_id: str):
    """Devuelve un callback que publica el avance del OCR en `document_ocr_progress`.

    Usa una transacción aparte porque la transacción principal del pipeline
    mantiene bloqueada la fila de `documents` hasta el commit final; escribir el
    progreso ahí se bloquearía. Errores de progreso nunca tumban el OCR.
    """
    def cb(done: int, total: int, detail: str = "") -> None:
        pct = min(100, int(100 * done / total)) if total else 0
        try:
            with tx(org_id, user_id) as c2:
                one(c2, """
                    INSERT INTO document_ocr_progress (document_id, organization_id, pct, detail, updated_at)
                    VALUES (:d, :o, :p, :det, now())
                    ON CONFLICT (document_id) DO UPDATE
                      SET pct = EXCLUDED.pct, detail = EXCLUDED.detail, updated_at = now()
                    RETURNING document_id
                """, d=document_id, o=org_id, p=pct, det=detail)
        except Exception:  # noqa: BLE001
            log.debug("no se pudo publicar el progreso OCR de %s", document_id, exc_info=True)
    return cb


def _clear_progress(org_id: str, user_id: str, document_id: str) -> None:
    try:
        with tx(org_id, user_id) as c2:
            one(c2, "DELETE FROM document_ocr_progress WHERE document_id = :d RETURNING document_id", d=document_id)
    except Exception:  # noqa: BLE001
        log.debug("no se pudo limpiar el progreso OCR de %s", document_id, exc_info=True)


def process_document(
    conn: Connection,
    document_id: str,
    org_id: str,
    case_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Ejecuta OCR sobre un documento, guarda páginas e imágenes, y actualiza el documento.

    Devuelve un resumen con `pages`, `needs_review_count`, `document_type`.
    """
    s = get_settings()
    allowed_statuses = {"UPLOADED", "OCR_PENDING", "OCR_COMPLETE", "REVIEW_REQUIRED"}
    # SELECT + UPDATE atómico para evitar race conditions entre workers.
    doc = one(conn, """
        UPDATE documents
        SET processing_status = 'OCR_RUNNING'
        WHERE id = :d AND case_id = :c AND processing_status = ANY(:allowed)
        RETURNING id, storage_uri, filename, mime_type, processing_status, ocr_mode
    """, d=document_id, c=case_id, allowed=list(allowed_statuses))
    if doc is None:
        existing = one(conn, "SELECT processing_status FROM documents WHERE id = :d AND case_id = :c",
                       d=document_id, c=case_id)
        if existing is None:
            raise PipelineError("document_not_found", f"document {document_id} not visible")
        log.info("doc %s already processed (status=%s); skipping", document_id, existing["processing_status"])
        return {"pages": 0, "needs_review_count": 0, "document_type": "other/unknown", "skipped": True}

    try:
        key = key_from_uri(doc["storage_uri"])
        document_bytes = storage().get(key)
    except Exception as exc:
        log.exception("no se pudo leer el documento %s desde storage", document_id)
        raise PipelineError("storage_error", str(exc)) from exc

    # Determinar proveedor OCR según el modo del documento.
    # None = default del sistema; "basico" = RapidOCR local (rápido); "document_ai" = Google.
    doc_mode = doc.get("ocr_mode")
    actual_mode = doc_mode  # motor realmente usado (puede diferir si hay fallback)
    if doc_mode == "document_ai":
        provider = OCR_PROVIDER_CLASSES["document_ai"]()
    elif doc_mode == "basico":
        provider = OCR_PROVIDER_CLASSES["docling"]()
    else:
        provider = get_ocr_provider()  # default del sistema

    try:
        progress_cb = _progress_writer(org_id, user_id, document_id)
        ocr_pages = provider.process(document_bytes, doc["mime_type"], progress_cb=progress_cb)
        # Document AI puede degradar a Docling: respetamos el motor real para no
        # etiquetar un resultado local como "document_ai".
        actual_mode = getattr(provider, "last_engine", doc_mode)
    except pytesseract.TesseractNotFoundError as exc:
        raise PipelineError("provider_unavailable", f"tesseract no encontrado: {exc}") from exc
    except Exception as exc:
        log.exception("OCR falló para %s", document_id)
        raise PipelineError("ocr_failed", str(exc)) from exc
    finally:
        _clear_progress(org_id, user_id, document_id)

    needs_review_count = 0
    dpi = s.OCR_DPI

    # Páginas ya corregidas por una persona: el reproceso automático no las pisa.
    protected_pages = {
        r["page_number"]
        for r in rows(conn, "SELECT page_number FROM document_pages "
                            "WHERE document_id = :d AND human_corrected", d=document_id)
    }

    with fitz.open(stream=document_bytes, filetype="pdf" if doc["mime_type"] == "application/pdf" else "png") as pdf:
        for ocr_page in ocr_pages:
            page_number = ocr_page.page_number
            page_idx = page_number - 1
            text = ocr_page.text
            # Confianza mostrada: arranca al 100% para el modo procesado y sólo baja si
            # la persona edita contenido real (ver services/ocr_confidence.py). La
            # confianza del motor ya no marca la página como "Revisar" (decisión de producto).
            confidence = 1.0
            folio = detect_folio(text)
            needs_review = False

            # Guarda imagen rasterizada para el visor (aunque la página esté corregida a mano).
            image_uri = ""
            if page_idx < pdf.page_count:
                image_bytes = _render_page_image(pdf.load_page(page_idx), dpi)
                img_key = _page_image_key(case_id, document_id, page_number)
                image_uri = storage().put(img_key, image_bytes)

            if page_number in protected_pages:
                # Conserva el texto corregido por la persona; sólo refresca la imagen y el folio.
                one(conn, "UPDATE document_pages SET image_uri = :img WHERE document_id = :d AND page_number = :pn RETURNING id",
                    img=image_uri, d=document_id, pn=page_number)
                continue

            # Estructuración con el modelo OCR activo (OPCIONAL, por defecto OFF).
            # Es costosa (~2-3 s por página) y su resultado sólo se mostraba en el
            # tab "Campos", ya retirado. Actívala con OCR_STRUCTURE_WITH_MODEL=true.
            structured = None
            if _STRUCTURE_WITH_MODEL:
                structured = _structure_ocr_page(conn, org_id, case_id, user_id, text, page_number)

            layout: dict[str, Any] = {}
            if ocr_page.words:
                layout["words"] = ocr_page.words[:100]
            if structured:
                layout["structured"] = structured
            if not layout:
                layout = None

            one(conn, """
                INSERT INTO document_pages (
                    organization_id, document_id, page_number, folio, image_uri,
                    text, ocr_confidence, needs_review, layout_json
                ) VALUES (
                    :o, :d, :pn, :folio, :img, :text, :conf, :review, :layout
                )
                ON CONFLICT (document_id, page_number) DO UPDATE SET
                    folio = EXCLUDED.folio,
                    image_uri = EXCLUDED.image_uri,
                    text = EXCLUDED.text,
                    ocr_confidence = EXCLUDED.ocr_confidence,
                    needs_review = EXCLUDED.needs_review,
                    layout_json = EXCLUDED.layout_json
                RETURNING id
            """,
                o=org_id, d=document_id, pn=page_number, folio=folio,
                img=image_uri, text=text, conf=float(confidence), review=needs_review,
                layout=_serialize_layout(layout))

            # Guarda la versión por motor para poder comparar Básico vs Document AI.
            if actual_mode in ("basico", "document_ai"):
                one(conn, """
                    INSERT INTO document_ocr_versions (
                        organization_id, document_id, page_number, mode, text, ocr_confidence, layout_json
                    ) VALUES (
                        :o, :d, :pn, :mode, :text, :conf, :layout
                    )
                    ON CONFLICT (document_id, page_number, mode) DO UPDATE SET
                        text = EXCLUDED.text,
                        ocr_confidence = EXCLUDED.ocr_confidence,
                        layout_json = EXCLUDED.layout_json,
                        created_at = now()
                    RETURNING id
                """,
                    o=org_id, d=document_id, pn=page_number, mode=actual_mode,
                    text=text, conf=float(confidence), layout=_serialize_layout(layout))

    # Clasificación documental: concatena texto de todas las páginas.
    full_text = "\n".join(p.text for p in ocr_pages)
    document_type, classification_reason = classify_document(full_text)

    one(conn, """
        UPDATE documents
        SET processing_status = :status,
            page_count = :pc,
            document_type = :dt,
            ocr_mode = :mode,
            parser_version = :pv
        WHERE id = :d
        RETURNING id
    """,
        status="REVIEW_REQUIRED" if needs_review_count else "OCR_COMPLETE",
        pc=len(ocr_pages), dt=document_type,
        mode=actual_mode if actual_mode in ("basico", "document_ai") else None,
        pv=s.PIPELINE_VERSION, d=document_id)

    return {
        "pages": len(ocr_pages),
        "needs_review_count": needs_review_count,
        "document_type": document_type,
        "classification_reason": classification_reason,
        "confidence_avg": sum(p.confidence for p in ocr_pages) / len(ocr_pages) if ocr_pages else 0.0,
    }


def classify_and_index_document(
    conn: Connection,
    document_id: str,
    case_id: str,
) -> dict[str, Any]:
    """Job complementario: reclasifica con LLM si está disponible y pasa a INDEXED."""
    doc = one(conn, "SELECT id, processing_status, document_type FROM documents "
                    "WHERE id = :d AND case_id = :c", d=document_id, c=case_id)
    if doc is None:
        raise PipelineError("document_not_found", f"document {document_id} not visible")

    status = doc["processing_status"]
    if status in {"OCR_COMPLETE", "REVIEW_REQUIRED"}:
        target = "INDEXED"
    elif status == "INDEXED":
        return {"document_id": document_id, "document_type": doc["document_type"], "skipped": True}
    else:
        raise PipelineError("invalid_state", f"cannot classify document in status {status}")

    # Reclasificación con LLM usando el texto ya extraído.
    page_rows = rows(conn, "SELECT text FROM document_pages WHERE document_id = :d ORDER BY page_number", d=document_id)
    full_text = "\n".join(r["text"] for r in page_rows)
    document_type, reason = classify_document(full_text) if full_text else (doc["document_type"], "sin texto")

    one(conn, "UPDATE documents SET processing_status = :s, document_type = :dt WHERE id = :d RETURNING id",
        s=target, dt=document_type, d=document_id)
    return {"document_id": document_id, "document_type": document_type, "reason": reason, "status": target}
