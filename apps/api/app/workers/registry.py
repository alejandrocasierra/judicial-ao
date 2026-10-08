"""Registro job_type -> handler (SSD §23, plan §0.2).

Los handlers de Fase 0 son stubs estructurados: no procesan nada todavía, pero
devuelven un resultado que el executor persiste en `model_runs` como constancia
 de la no-implementación. Se sustituyen por la lógica real en las fases 2–5 del
plan. Un job_type sin handler registrado es un fallo determinista: el executor
lo marca FAILED sin reintento (política de `app/domain/states.py`).
"""
from __future__ import annotations

from typing import Any, Callable

from app.workers.handlers import document_classification, document_ocr, embedding, file_ingest, graph_build, indexing, legal_extraction, media_asr, media_diarize, procedural_links, xlsx_ingest

# Un handler recibe el job (dict de la fila `jobs`) y devuelve el resultado
# que el executor persiste.
Handler = Callable[[dict[str, Any]], dict[str, Any]]

# Fase del plan de trabajo en la que cada tipo recibe su implementación real.
PENDING_PHASE = {
    "document_ocr": "fase-2",
    "document_classification": "fase-2",
    "media_asr": "fase-3",
    "diarization": "fase-3",
    "legal_extraction": "fase-4",
    "embedding": "fase-4",
    "indexing": "fase-4",
    "graph_build": "fase-6",
    "file_ingest": "procesos",
    "xlsx_ingest": "procesos",
    "procedural_links": "procesos",
}


def _stub(job_type: str) -> Handler:
    def handler(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "implemented": False,
            "pending_phase": PENDING_PHASE[job_type],
            "job_type": job_type,
            "input_count": len(job["input_ids"]),
            "note": "stub de Fase 0: el handler real se implementa en la fase indicada",
        }

    return handler


HANDLERS: dict[str, Handler] = {
    "document_ocr": document_ocr.handle,
    "document_classification": document_classification.handle,
    "media_asr": media_asr.handle,
    "media_diarize": media_diarize.handle,
    "legal_extraction": legal_extraction.handle,
    "embedding": embedding.handle,
    "indexing": indexing.handle,
    "graph_build": graph_build.handle,
    "file_ingest": file_ingest.handle,
    "xlsx_ingest": xlsx_ingest.handle,
    "procedural_links": procedural_links.handle,
}

for _jt in PENDING_PHASE:
    if _jt not in HANDLERS:
        HANDLERS[_jt] = _stub(_jt)
