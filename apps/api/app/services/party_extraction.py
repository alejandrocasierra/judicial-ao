"""Extracción de PARTES (demandante/demandado/ejecutante/…) desde los encabezados de los autos.

Heurística ESTRICTA sobre el OCR: reconoce el cargo de parte (demandante/demandado/ejecutante/…)
y toma el TRAMO INICIAL en mayúsculas a su lado como nombre; clasifica persona/ organización y
deduplica por nombre normalizado. Pensada para REVISIÓN humana (panel) antes de confirmar.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import rows

# Palabra de rol → rol canónico de `parties.role` (solo partes: demandante/demandado).
_ROLE_MAP: dict[str, str] = {
    "demandante": "claimant", "demandantes": "claimant", "demandada": "claimant", "demandadas": "claimant",
    "ejecutante": "claimant", "ejecutantes": "claimant", "denunciante": "claimant", "denunciantes": "claimant",
    "accionante": "claimant", "accionantes": "claimant", "actor": "claimant", "actores": "claimant",
    "demandado": "defendant", "demandados": "defendant", "denunciado": "defendant", "denunciados": "defendant",
    "ejecutado": "defendant", "ejecutados": "defendant",
}

_ROLE_WORDS = ("demandantes?|demandadas?|ejecutantes?|ejecutados?|denunciantes?|denunciados?|"
               "accionantes?|actores?")
# "DEMANDANTE: Nombre" / "DEMANDADO - Nombre"
_RE_AFTER = re.compile(rf"(?i:\b({_ROLE_WORDS})\b)\s*[:.\-–]\s*([^\n;|]{{3,90}})")
# "Nombre DEMANDADO" / "Nombre, DEMANDANTE"  (el nombre va antes del cargo)
_RE_BEFORE = re.compile(rf"(?im)^\s*([^\n;:|]{{3,80}}?)\s*,?\s*(?i:\b({_ROLE_WORDS})\b)\s*[:.\-–]?\s*$")

_ORG_RE = re.compile(r"(?i)\b(s\.?\s?a\.?\s?s?\.?|s\.?\s?a\.?|ltda\.?|limitada|e\.?\s?u\.?|"
                     r"corp(oraci[oó]n)?|sociedad|fondo|banco|aseguradora|consorcio|uni[oó]n temporal|inversiones|"
                     r"comercializadora|constructora|servicios)\b")

# Palabras que NO forman parte de un nombre de parte.
_STOP = {
    "EL", "LA", "LOS", "LAS", "DE", "DEL", "Y", "EN", "POR", "CON", "SEÑOR", "SEÑORA", "SR", "SRA",
    "PARTE", "PARTES", "DEMANDANTE", "DEMANDADO", "DEMANDADOS", "DEMANDADAS", "EJECUTANTE", "EJECUTADO",
    "ACTOR", "ACCIONANTE", "JUDICIAL", "CALIDAD", "COMO", "OBRA", "ACTUANDO", "FIRMA", "CORRESPONDIENTE",
    "EXTREMO", "PROCESO", "EXPEDIENTE", "RADICADO", "JUZGADO", "DESPACHO", "CIRCUITO", "MUNICIPAL",
    "AUTOS", "AUTO", "REF", "REFERENCIA", "DEMANDA", "PROCESAL", "DOCTOR", "DOCTORA", "ABOGADO",
    "ABOGADA", "APODERADO", "APODERADA", "REPRESENTANTE", "REPRESENTANTES", "MEDIANTE", "DENTRO",
    "CONTRA", "FAVOR", "SEGUNDO", "TERCERO", "PRIMERO", "RESPECTO", "SOBRE", "SEÑALA", "INFORMA",
    "NO", "SI", "PRUEBA", "PRINCIPAL", "IDENTIFICADO", "IDENTIFICADA", "IDENTIFICACION",
    "DOMICILIADO", "DOMICILIADA", "PORTADOR", "PORTADORA", "MAYOR", "MENOR", "EDAD",
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", strip_accents((name or "").upper()))


def entity_type(name: str) -> str:
    return "organization" if _ORG_RE.search(name or "") else "person"


def _is_name_token(tok: str) -> bool:
    t = tok.strip(".,;:()[]")
    if len(t) < 2:
        return False
    if t.upper() in _STOP:
        return False
    return bool(re.match(r"^[A-ZÁÉÍÓÚÜÑ0-9]", t))


def _clean(raw: str) -> str | None:
    """Toma el TRAMO INICIAL de tokens tipo-nombre (mayúscula inicial) y descarta el resto."""
    s = re.sub(r"^[\s,;.:\-–·]+", "", (raw or "").strip())
    keep: list[str] = []
    for tok in s.split():
        if _is_name_token(tok):
            keep.append(tok.strip(".,;:()[]"))
            if len(keep) == 5:
                break
        else:
            break
    name = re.sub(r"\s+", " ", " ".join(keep)).strip(" ,;.:-–")
    # 2-4 tokens tipo-nombre, con al menos uno "de verdad" (letra, no solo números).
    if not (2 <= len(keep) <= 4) or len(name) < 5:
        return None
    if not any(len(t) >= 3 and t[:1].isalpha() for t in keep):
        return None
    return name


def extract(conn: Connection, case_id: str) -> list[dict[str, Any]]:
    """Devuelve candidatos de partes (nombre, rol, tipo, menciones y cita). No persiste nada."""
    pages = rows(conn, """
        SELECT p.document_id, d.filename, p.page_number, p.folio, p.text
        FROM document_pages p JOIN documents d ON d.id = p.document_id
        WHERE d.case_id = :c
        ORDER BY d.filename, p.page_number
    """, c=case_id)
    cands: dict[str, dict[str, Any]] = {}

    def add(raw: str, role_word: str, pg: dict[str, Any]) -> None:
        name = _clean(raw)
        if not name:
            return
        key = normalize_name(name)
        if not key:
            return
        role = _ROLE_MAP.get(role_word.lower().strip("."), "third_party")
        cur = cands.get(key)
        if cur is None:
            cands[key] = {"name": name, "normalized_name": key, "role": role,
                          "entity_type": entity_type(name), "mentions": 1,
                          "document_id": str(pg["document_id"]), "filename": pg["filename"],
                          "page_number": pg["page_number"], "folio": pg["folio"], "aliases": []}
        else:
            cur["mentions"] += 1

    for pg in pages:
        txt = pg["text"] or ""
        for m in _RE_AFTER.finditer(txt):
            add(m.group(2), m.group(1), pg)
        for m in _RE_BEFORE.finditer(txt):
            add(m.group(1), m.group(2), pg)
    return sorted(cands.values(), key=lambda c: -c["mentions"])


def create_parties(conn: Connection, org_id: str, case_id: str, actor_id: str | None,
                   items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inserta las partes indicadas, evitando duplicados por nombre normalizado. Devuelve las nuevas."""
    existing = {r["normalized_name"] for r in rows(
        conn, "SELECT normalized_name FROM parties WHERE case_id = :c", c=case_id)}
    created: list[dict[str, Any]] = []
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        key = normalize_name(name)
        if not key or key in existing:
            continue
        role = it.get("role") if it.get("role") in set(_ROLE_MAP.values()) else "third_party"
        etype = it.get("entity_type") if it.get("entity_type") in ("person", "organization") else entity_type(name)
        aliases = [a for a in (it.get("aliases") or []) if a]
        row = conn.execute(text("""INSERT INTO parties (organization_id, case_id, name, normalized_name, role,
                                       entity_type, aliases)
            VALUES (:o, :c, :n, :nn, :r, :e, :al) RETURNING id, name, role, entity_type, aliases"""),
            {"o": org_id, "c": case_id, "n": name, "nn": key, "r": role, "e": etype, "al": aliases}).mappings().first()
        existing.add(key)
        created.append(dict(row))
    return created
