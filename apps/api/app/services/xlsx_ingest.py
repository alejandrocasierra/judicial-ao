"""Ingesta automática de índices XLSX del expediente (Fase 1 del plan, módulo Procesos).

Cada vez que se sube un .xlsx al gestor de archivos de un proceso:
  1. Se parsea con `index_xlsx` (el XLSX es la fuente de verdad procesal, D5).
  2. Índice general: se garantizan las carpetas de cada instancia/cuaderno que lista.
  3. Índice de cuaderno: se valida su carátula (radicación contra el caso).
  4. El modelo de IA marcado como OCR (`ai_models.ocr_enabled`) analiza el índice
     (prompt versionado `analyze_index.v1.md`) y su análisis queda guardado como
     archivo Markdown junto al Excel (`AnalisisIA_<nombre>.md`), además del
     registro técnico en `model_runs` y la auditoría.
"""
from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import one
from app.providers.llm import get_llm_for_model
from app.services import audit
from app.services.index_xlsx import CuadernoIndex, GeneralIndex, parse_cuaderno_index, parse_general_index
from app.services.legal_extraction import _check_budget, _load_prompt, _spend_budget
from app.services.storage import key_from_uri, storage

log = logging.getLogger(__name__)

GENERAL_INDEX_NAME = "0000indiceexpedientegeneral.xlsx"
ANALYSIS_MIME = "text/markdown"
_MAX_ENTRIES_FOR_LLM = 150
_MAX_NAME_CHARS = 140


def ensure_folder_path(conn: Connection, org_id: str, case_id: str, user_id: str | None,
                       parts: list[str]) -> str | None:
    """Garantiza (idempotente) una ruta de carpetas anidadas; devuelve el id de la última."""
    parent_id: str | None = None
    for raw in parts:
        name = raw.strip()
        if not name:
            continue
        row = one(conn, """SELECT id FROM case_folders
                           WHERE case_id = :c AND parent_id IS NOT DISTINCT FROM :p AND lower(name) = lower(:n)""",
                  c=case_id, p=parent_id, n=name)
        if row is None:
            row = one(conn, """INSERT INTO case_folders (organization_id, case_id, parent_id, name, created_by)
                               VALUES (:o,:c,:p,:n,:u) RETURNING id""",
                      o=org_id, c=case_id, p=parent_id, n=name, u=user_id)
        parent_id = str(row["id"])
    return parent_id


def folder_path_parts(conn: Connection, folder_id: str | None) -> list[str]:
    """Ruta (raíz -> hoja) de una carpeta, para reconstruir el nombre del cuaderno."""
    parts: list[str] = []
    cur = folder_id
    while cur:
        row = one(conn, "SELECT id, parent_id, name FROM case_folders WHERE id = :f", f=cur)
        if row is None:
            break
        parts.insert(0, row["name"])
        cur = str(row["parent_id"]) if row["parent_id"] else None
    return parts


def _parse_index(data: bytes, filename: str, cuaderno: str) -> tuple[GeneralIndex | CuadernoIndex, bool]:
    """Parsea el XLSX desde bytes. Devuelve (índice, es_general)."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        if filename.lower() == GENERAL_INDEX_NAME:
            return parse_general_index(tmp_path), True
        return parse_cuaderno_index(tmp_path, cuaderno), False
    finally:
        tmp_path.unlink(missing_ok=True)


def _entries_lines(idx: GeneralIndex | CuadernoIndex, is_general: bool) -> list[str]:
    if is_general:
        return [f"- {r.orden or '—'} | {r.nombre}" for r in idx.cuadernos[:_MAX_ENTRIES_FOR_LLM]]
    lines = []
    for e in idx.entries[:_MAX_ENTRIES_FOR_LLM]:
        lines.append(
            f"- {e.indice_numero or '—'} | {e.nombre_original[:_MAX_NAME_CHARS]} | creado: {e.fecha_creacion or '—'}"
            f" | incorporado: {e.fecha_incorporacion or '—'} | páginas: {e.numero_paginas or '—'}"
            f" | formato: {e.formato or '—'}"
        )
    return lines


def _llm_content(idx: GeneralIndex | CuadernoIndex, is_general: bool, filename: str, cuaderno: str) -> str:
    m = idx.metadata
    kind = "general" if is_general else "cuaderno"
    entries = _entries_lines(idx, is_general)
    total_entries = len(idx.cuadernos) if is_general else len(idx.entries)
    truncated = "" if total_entries <= _MAX_ENTRIES_FOR_LLM else f"\n(truncado: se muestran {_MAX_ENTRIES_FOR_LLM} de {total_entries} ítems)"
    return (
        f'<untrusted_index filename="{filename}" kind="{kind}" cuaderno="{cuaderno}">\n'
        f"radicacion: {m.radicacion or '—'}\n"
        f"ciudad: {m.ciudad or '—'}\n"
        f"despacho: {m.despacho or '—'}\n"
        f"serie: {m.serie or '—'}\n"
        f"parte_a: {m.parte_a or '—'}\n"
        f"parte_b: {m.parte_b or '—'}\n"
        f"items: {total_entries}{truncated}\n"
        + "\n".join(entries)
        + "\n</untrusted_index>"
    )


def _ocr_model(conn: Connection) -> dict | None:
    """Modelo marcado con el flag OCR (RLS limita a la organización actual)."""
    return one(conn, "SELECT id, provider, model_name FROM ai_models WHERE ocr_enabled LIMIT 1")


def _analyze_with_ocr_model(conn: Connection, org_id: str, case_id: str, user_id: str,
                            idx: GeneralIndex | CuadernoIndex, is_general: bool,
                            filename: str, cuaderno: str) -> dict[str, Any]:
    """Analiza el índice con el modelo marcado como OCR. Nunca rompe la ingesta:
    si el modelo no existe, no hay presupuesto o la llamada falla, devuelve el motivo."""
    model = _ocr_model(conn)
    if model is None:
        return {"status": "skipped", "reason": "no_ocr_model"}
    content = _llm_content(idx, is_general, filename, cuaderno)
    estimated = (len(content) + 3000) // 4
    if not _check_budget(conn, case_id, estimated):
        return {"status": "skipped", "reason": "budget_exceeded"}
    try:
        body, prompt_id, prompt_version = _load_prompt("analyze_index", "es")
        llm = get_llm_for_model(str(model["id"]), org_id, user_id)
        result = llm.complete(body, content)
        _spend_budget(conn, case_id, result.tokens_in, result.tokens_out)
        one(conn, """INSERT INTO model_runs (organization_id, case_id, task, provider, model,
                        prompt_id, prompt_version, pipeline_version, input_hash, output, tokens_in, tokens_out, actor_id)
                     VALUES (:o,:c,:t,:p,:m,:pid,:pv,'1.0',:ih,CAST(:out AS jsonb),:ti,:to,:a) RETURNING id""",
            o=org_id, c=case_id, t="xlsx_ingest_analysis", p=result.provider, m=result.model,
            pid=prompt_id, pv=prompt_version, ih=hashlib.sha256(content.encode()).hexdigest(),
            out=json.dumps({"filename": filename, "cuaderno": cuaderno, "chars": len(result.text)}),
            ti=result.tokens_in, to=result.tokens_out, a=user_id)
        return {"status": "ok", "text": result.text, "provider": result.provider, "model": result.model}
    except Exception as exc:  # noqa: BLE001
        log.exception("análisis IA del índice %s falló", filename)
        return {"status": "error", "reason": str(exc)[:300]}


def _save_analysis_file(conn: Connection, org_id: str, case_id: str, user_id: str,
                        folder_id: str | None, source_filename: str, markdown: str) -> dict:
    """Guarda el análisis como archivo Markdown junto al Excel (reemplaza el anterior)."""
    name = f"AnalisisIA_{source_filename.rsplit('.', 1)[0]}.md"
    conn.execute(text("""DELETE FROM case_files
                         WHERE case_id = :c AND folder_id IS NOT DISTINCT FROM :f AND filename = :n"""),
                 {"c": case_id, "f": folder_id, "n": name})
    data = markdown.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    uri = storage().put(f"cases/{case_id}/files/{sha}", data)
    return one(conn, """INSERT INTO case_files (organization_id, case_id, folder_id, storage_uri, sha256,
                            size_bytes, mime_type, filename, uploaded_by)
                        VALUES (:o,:c,:f,:u,:h,:sz,:m,:n,:by) RETURNING id, filename""",
               o=org_id, c=case_id, f=folder_id, u=uri, h=sha, sz=len(data), m=ANALYSIS_MIME, n=name, by=user_id)


def ingest_xlsx(conn: Connection, *, org_id: str, case_id: str, user_id: str,
                case_file: dict, case_number: str) -> dict[str, Any]:
    """Ingesta un .xlsx subido al proceso: parseo + carpetas + validación + análisis IA."""
    data = storage().get(key_from_uri(case_file["storage_uri"]))
    filename = case_file["filename"]
    folder_id = str(case_file["folder_id"]) if case_file["folder_id"] else None
    cuaderno = "/".join(folder_path_parts(conn, folder_id))

    idx, is_general = _parse_index(data, filename, cuaderno)
    folders_created: list[str] = []
    if is_general:
        for ref in idx.cuadernos:
            parts = [p for p in ref.nombre.split("/") if p.strip()]
            if parts:
                fid = ensure_folder_path(conn, org_id, case_id, user_id, parts)
                folders_created.append("/".join(parts) + ("" if fid else " (existente)"))

    m = idx.metadata
    radicacion_ok = (not m.radicacion) or (m.radicacion == case_number)
    total_entries = len(idx.cuadernos) if is_general else len(idx.entries)

    analysis = _analyze_with_ocr_model(conn, org_id, case_id, user_id, idx, is_general, filename, cuaderno)
    analysis_file = None
    if analysis["status"] == "ok":
        analysis_file = _save_analysis_file(conn, org_id, case_id, user_id, folder_id, filename, analysis["text"])

    result = {
        "filename": filename,
        "kind": "general" if is_general else "cuaderno",
        "cuaderno": cuaderno or None,
        "entries": total_entries,
        "radicacion": m.radicacion,
        "radicacion_ok": radicacion_ok,
        "partes": [p for p in (m.parte_a, m.parte_b) if p],
        "folders_ensured": folders_created,
        "analysis": {k: v for k, v in analysis.items() if k != "text"},
        "analysis_file": analysis_file["filename"] if analysis_file else None,
    }
    audit.record(conn, org_id=org_id, actor_id=user_id, action="file.index_ingested",
                 entity_type="file", entity_id=str(case_file["id"]), after=result)
    return result
