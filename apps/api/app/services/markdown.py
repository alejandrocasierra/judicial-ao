"""Representación Markdown de un documento OCR.

Convierte el texto plano por página (document_pages) en un Markdown legible y con
jerarquía (títulos, listas y párrafos), útil para lectura contextual por un LLM y
para exportarlo en el CKP. No inventa contenido: solo estructura lo que ya hay.
"""
from __future__ import annotations

import re

from sqlalchemy.engine import Connection

from app.core.db import one, rows

# Encabezados jurídicos frecuentes (se promueven a título aunque sean cortos).
_KNOWN_HEADINGS = {
    "HECHOS", "PRETENSIONES", "PETICIONES", "FUNDAMENTOS", "FUNDAMENTOS DE DERECHO",
    "CONTESTACIÓN", "CONTESTACION", "RESUELVE", "RESUELVO", "CONSIDERANDO", "CONSIDERACIONES",
    "ANTECEDENTES", "DECIDE", "DECISIÓN", "DECISION", "RESUMEN", "ASUNTO", "PARTES", "PRUEBAS",
    "ANEXOS", "DECLARACIONES", "NOTIFICACIÓN", "NOTIFICACION", "APELACIÓN", "APELACION",
    "ANTECEDENTES PROCESALES", "ACTUACIÓN PROCESAL", "ACTUACION PROCESAL", "COMPETENCIA",
    "LEGITIMACIÓN", "LEGITIMACION", "PROCEDIMIENTO", "CONCLUSIONES", "SOLICITUD", "PODER",
}
_ORDINAL_RE = re.compile(
    r"^(PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|S[ÉE]PTIMO|OCTAVO|NOVENO|D[ÉE]CIMO)\b", re.IGNORECASE)
_LIST_RE = re.compile(r"^\s*(?:[-•*·▪◦]|\d{1,3}[.)])\s+")
_MAX_HEADING_LEN = 90


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not (2 <= len(s) <= _MAX_HEADING_LEN):
        return False
    if s.endswith((".", ";", ",")):
        return False
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    if s.upper().strip(":") in _KNOWN_HEADINGS:
        return True
    if _ORDINAL_RE.match(s):
        return True
    if upper_ratio >= 0.85 and len(s) >= 4:
        return True
    return bool(s.endswith(":") and len(s) <= 60 and upper_ratio >= 0.5)


def text_to_markdown(text: str | None) -> str:
    """Estructura el texto de una página: encabezados `###`, listas y párrafos."""
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append(" ".join(x.strip() for x in para).strip())
            out.append("")
            para.clear()

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        m = _LIST_RE.match(line)
        if _is_heading(line):
            flush()
            out.append(f"### {line}")
            out.append("")
        elif m:
            flush()
            marker = m.group(0).strip()
            rest = line[len(m.group(0)):].strip()
            out.append(f"1. {rest}" if marker[0].isdigit() else f"- {rest}")
        else:
            para.append(line)
    flush()
    return "\n".join(out).strip()


def document_markdown(conn: Connection, case_id: str, document_id: str) -> str | None:
    """Markdown completo de un documento (metadatos + una sección por página).

    Devuelve None si el documento no existe en el expediente."""
    doc = one(conn, """SELECT filename, document_type, document_date, page_count, ocr_mode
        FROM documents WHERE id = :d AND case_id = :c""", d=document_id, c=case_id)
    if not doc:
        return None
    pages = rows(conn, """SELECT page_number, folio, text FROM document_pages
        WHERE document_id = :d ORDER BY page_number""", d=document_id)

    header = [f"# {doc['filename']}", ""]
    meta = []
    if doc.get("document_type"):
        meta.append(f"**Tipo:** {doc['document_type']}")
    if doc.get("document_date"):
        meta.append(f"**Fecha:** {doc['document_date']}")
    meta.append(f"**Páginas:** {doc.get('page_count') or len(pages)}")
    if doc.get("ocr_mode"):
        meta.append(f"**Modo OCR:** {doc['ocr_mode']}")
    header += ["  \n".join(meta), "", "---", ""]

    body: list[str] = []
    for pg in pages:
        folio = f" (folio {pg['folio']})" if pg.get("folio") else ""
        body.append(f"## Página {pg['page_number']}{folio}")
        body.append("")
        body.append(text_to_markdown(pg.get("text")))
        body.append("")
        body.append("---")
        body.append("")
    return "\n".join(header + body).strip() + "\n"
