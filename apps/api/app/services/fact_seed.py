"""Siembra de hechos (`facts`) con su cita primaria resuelta.

El banco de hechos ya sabe qué está probado y qué solo se alega, pero el chat no
lo usaba: la búsqueda híbrida de fragmentos devolvía páginas sueltas y el modelo
tenía que adivinar el estado epistémico. Aquí se seleccionan los hechos del caso
que responden a la pregunta y se inyectan como evidencia de primera clase, con
la página o el segmento que los respalda.

Regla dura: **este módulo nunca inventa una cita.** Si un hecho no resuelve a una
página o a un segmento concreto (con rank léxico > 0 y al menos dos términos del
hecho presentes), el hecho se omite. Está prohibido asignarle la primera página
del documento o una página «probable».

Orden de resolución de la cita (contrato F2 · R3):

1. `citations` con `target_type='fact'` (aún puede estar vacío),
2. `fact_positions.source_citation_id`,
3. `evidence_links` → `evidence.source_document_id` / `source_media_id` y, dentro
   de esa fuente, el chunk/segmento con mayor `ts_rank_cd` para una `to_tsquery`
   OR de los términos del hecho,
4. si el hecho no tiene fuente vinculada, todos los chunks/segmentos del caso
   con la misma regla y umbral,
5. si nada resuelve, el hecho se descarta.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.engine import Connection

from app.core.db import rows
from app.services import text_terms

# Ancla normativa: en usura/capitalización el expediente suele citar el artículo
# 886 del Código de Comercio. Se inyecta el pasaje que lo menciona para que la
# síntesis pueda apoyarse en la fuente, no en memoria del modelo.
_LEGAL_QUESTION = re.compile(r"\b(usura|capitalizacion|anatocismo)\b")
_INVENTORY_QUESTION = re.compile(
    r"\b(cuantos|cuantas|cuales|que|lista|listado|inventario)\b"
    r"[^?]{0,50}\b(pagares?|titulos?|obligaciones)\b")
_LEGAL_ANCHOR = re.compile(r"\b(886|articulo\s+886|codigo de comercio)\b", re.I)
_DATE_IN_FACT = re.compile(
    r"\b(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|setiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?(\d{4})\b",
    re.I,
)
_MONTH_SHORT = {
    "enero": "Ene", "febrero": "Feb", "marzo": "Mar", "abril": "Abr",
    "mayo": "May", "junio": "Jun", "julio": "Jul", "agosto": "Ago",
    "septiembre": "Sep", "setiembre": "Sep", "octubre": "Oct",
    "noviembre": "Nov", "diciembre": "Dic",
}

# Mínimo de términos de contenido en la pregunta para intentar la siembra.
MIN_TERMS = 2
# Términos distintos del hecho que deben aparecer en la fuente para aceptarla.
MIN_FACT_TERMS_IN_SOURCE = 2
# Candidatos léxicos por chunk/segmento que se revisan antes de descartar.
CHUNK_CANDIDATES = 8
PAGE_CHARS = 1800
SEGMENT_CHARS = 1200
# Segmentos de audio que se conservan tras ordenarlos por densidad sustantiva.
MAX_AUDIO_SEEDS = 10
# Prioridad epistémica: probado/admitido primero, luego sostenido por evidencia.
_STATUS_PRIORITY = {"JUDICIALLY_PROVEN": 0, "ADMITTED": 0, "EVIDENCE_SUPPORTED": 1}
_DEFAULT_PRIORITY = 2


def or_query(terms: list[str]) -> str:
    """`to_tsquery` OR de los términos, con cada lexema escapado entre comillas.

    Las comillas simples y las barras invertidas son los únicos caracteres que
    pueden romper la expresión o inyectar operadores; se sustituyen por espacio.
    El resto del texto se normaliza (minúsculas, sin diacríticos) porque
    `search_tsv` se genera sobre `immutable_unaccent(...)`.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for term in terms or []:
        folded = text_terms.norm(term)
        folded = "".join(" " if ch in "'\\" else ch for ch in folded)
        folded = " ".join(folded.split())
        if not folded or folded in seen:
            continue
        seen.add(folded)
        parts.append(f"'{folded}'")
    return " | ".join(parts)


def _priority(fact: dict[str, Any]) -> int:
    return _STATUS_PRIORITY.get(str(fact.get("proof_status") or ""), _DEFAULT_PRIORITY)


def _candidates(conn: Connection, case_id: str, query: str, limit: int) -> list[dict[str, Any]]:
    """Hechos del caso con rank léxico > 0 frente a la pregunta.

    El ordinal se calcula sobre TODOS los hechos del caso (`created_at`, `id`):
    así `fact_number` es estable entre preguntas aunque el hecho no tenga
    `fact_code`.
    """
    return rows(conn, """
        WITH case_facts AS (
            SELECT f.id::text AS id,
                   f.proposition,
                   f.proof_status,
                   f.fact_code,
                   row_number() OVER (ORDER BY f.created_at, f.id) AS ordinal
              FROM facts f
             WHERE f.case_id = :c
        )
        SELECT id, proposition, proof_status, fact_code, ordinal,
               ts_rank(to_tsvector('spanish', immutable_unaccent(proposition)),
                       to_tsquery('spanish', :q)) AS rank
          FROM case_facts
         WHERE to_tsvector('spanish', immutable_unaccent(proposition))
               @@ to_tsquery('spanish', :q)
         ORDER BY rank DESC, id
         LIMIT :n
    """, c=case_id, q=query, n=limit)


def _fact_terms(fact: dict[str, Any]) -> list[str]:
    return text_terms.content_terms(str(fact.get("proposition") or ""))


def _terms_present(text: str, terms: list[str]) -> set[str]:
    """Términos distintos del hecho que aparecen literalmente (o por raíz) en la fuente."""
    folded = text_terms.norm(text)
    stems = text_terms.content_set(text)
    found: set[str] = set()
    for term in terms:
        key = text_terms.norm(term)
        if not key:
            continue
        if key in folded or text_terms.stem(key) in stems:
            found.add(key)
    return found


def _accepts(text: str, terms: list[str]) -> bool:
    if not text:
        return False
    return len(_terms_present(text, terms)) >= MIN_FACT_TERMS_IN_SOURCE


# --- resolución de la cita ----------------------------------------------------

def _citation_sql(source: str) -> str:
    return f"""
        SELECT c.source_type, c.document_id, c.page_number, c.folio,
               c.media_id, c.segment_id, c.start_ms, c.end_ms,
               coalesce(dp.text, ts.text) AS text,
               coalesce(d.filename, m.filename) AS filename,
               sp.display_name AS speaker
          FROM {source} c
          LEFT JOIN document_pages dp ON dp.document_id = c.document_id
                                    AND dp.page_number = c.page_number
          LEFT JOIN documents d ON d.id = c.document_id
          LEFT JOIN transcript_segments ts ON ts.id = c.segment_id
          LEFT JOIN media m ON m.id = c.media_id
          LEFT JOIN speakers sp ON sp.id = ts.speaker_id
         WHERE /*FILTER*/
         ORDER BY c.page_number NULLS LAST, c.start_ms NULLS LAST
         LIMIT 1
    """


def _citation_row(conn: Connection, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    found = rows(conn, sql, **params)
    return found[0] if found else None


def _citation_by_target(conn: Connection, fact_id: str) -> dict[str, Any] | None:
    """1. Cita ya registrada para el hecho (`citations.target_type = 'fact'`)."""
    sql = _citation_sql("citations").replace("/*FILTER*/",
                                             "c.target_type = 'fact' AND c.target_id = :fact")
    return _citation_row(conn, sql, {"fact": fact_id})


def _citation_by_position(conn: Connection, fact_id: str) -> dict[str, Any] | None:
    """2. Cita de origen de una posición de parte (`fact_positions`)."""
    sql = _citation_sql("citations").replace(
        "/*FILTER*/", """c.id IN (SELECT fp.source_citation_id
                                     FROM fact_positions fp
                                    WHERE fp.fact_id = :fact
                                      AND fp.source_citation_id IS NOT NULL)""")
    return _citation_row(conn, sql, {"fact": fact_id})


def _linked_sources(conn: Connection, fact_id: str) -> tuple[list[str], list[str]]:
    """3. Documentos y medias vinculados al hecho por `evidence_links`."""
    found = rows(conn, """
        SELECT e.source_document_id::text AS document_id,
               e.source_media_id::text AS media_id,
               max(CASE WHEN el.stance = 'supports' THEN 1 ELSE 0 END) AS supports
          FROM evidence_links el
          JOIN evidence e ON e.id = el.evidence_id
         WHERE el.fact_id = :fact
           AND (e.source_document_id IS NOT NULL OR e.source_media_id IS NOT NULL)
         GROUP BY 1, 2
         ORDER BY supports DESC
    """, fact=fact_id)
    documents: list[str] = []
    medias: list[str] = []
    for row in found:
        if row.get("document_id") and row["document_id"] not in documents:
            documents.append(str(row["document_id"]))
        if row.get("media_id") and row["media_id"] not in medias:
            medias.append(str(row["media_id"]))
    return documents, medias


def _best_page(conn: Connection, case_id: str, query: str,
               terms: list[str], document_ids: list[str] | None) -> dict[str, Any] | None:
    """Chunk de documento con mayor `ts_rank_cd` para los términos del hecho.

    Se acepta solo si la **página citada** contiene al menos dos términos
    distintos del hecho: el rank elige el chunk, pero la cita que ve el usuario
    es la página completa.
    """
    scoped = ""
    params: dict[str, Any] = {"c": case_id, "q": query, "n": CHUNK_CANDIDATES}
    if document_ids:
        scoped = " AND c.document_id = ANY(CAST(:docs AS uuid[]))"
        params["docs"] = "{" + ",".join(document_ids) + "}"
    found = rows(conn, f"""
        SELECT c.document_id::text AS document_id,
               c.page_number,
               dp.folio,
               d.filename,
               coalesce(dp.text, c.text) AS text,
               ts_rank_cd(c.search_tsv, to_tsquery('spanish', :q)) AS rank
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
          LEFT JOIN document_pages dp ON dp.document_id = c.document_id
                                    AND dp.page_number = c.page_number
         WHERE c.case_id = :c
           AND c.document_id IS NOT NULL
           AND c.page_number IS NOT NULL
           AND c.search_tsv @@ to_tsquery('spanish', :q){scoped}
         ORDER BY rank DESC, c.page_number, c.id
         LIMIT :n
    """, **params)
    for row in found:
        if _accepts(str(row.get("text") or ""), terms):
            return row
    return None


def _best_segment(conn: Connection, case_id: str, query: str,
                  terms: list[str], media_ids: list[str] | None) -> dict[str, Any] | None:
    """Segmento ASR con mayor `ts_rank_cd` que contenga ≥2 términos distintos del hecho."""
    scoped = ""
    params: dict[str, Any] = {"c": case_id, "q": query, "n": CHUNK_CANDIDATES}
    if media_ids:
        scoped = " AND s.media_id = ANY(CAST(:mids AS uuid[]))"
        params["mids"] = "{" + ",".join(media_ids) + "}"
    found = rows(conn, f"""
        SELECT s.id::text AS segment_id,
               s.media_id::text AS media_id,
               s.start_ms, s.end_ms, s.text,
               m.filename,
               sp.display_name AS speaker,
               ts_rank_cd(s.search_tsv, to_tsquery('spanish', :q)) AS rank
          FROM transcript_segments s
          JOIN media m ON m.id = s.media_id
          LEFT JOIN speakers sp ON sp.id = s.speaker_id
         WHERE m.case_id = :c
           AND s.search_tsv @@ to_tsquery('spanish', :q){scoped}
         ORDER BY rank DESC, s.start_ms, s.id
         LIMIT :n
    """, **params)
    for row in found:
        if _accepts(str(row.get("text") or ""), terms):
            return row
    return None


# --- construcción del ítem ----------------------------------------------------

def _fact_fields(fact: dict[str, Any]) -> dict[str, Any]:
    number = str(fact.get("fact_code") or fact.get("ordinal") or "")
    status = str(fact.get("proof_status") or "ALLEGED")
    return {
        "seed": "fact",
        "fact_id": str(fact.get("id") or ""),
        "fact_number": number,
        "fact_text": str(fact.get("proposition") or ""),
        "proof_status": status,
        "fact_label": f"[Hecho #{number} · {status}]",
    }


def _page_item(fact: dict[str, Any], row: dict[str, Any], index: int) -> dict[str, Any] | None:
    text = " ".join(str(row.get("text") or "").split())[:PAGE_CHARS]
    if not text or row.get("document_id") is None or row.get("page_number") is None:
        return None
    item = {
        "handle": f"F{index}",
        "source_type": "document_page",
        "document_id": row.get("document_id"),
        "page_number": row.get("page_number"),
        "folio": row.get("folio"),
        "filename": row.get("filename"),
        "text": text,
    }
    item.update(_fact_fields(fact))
    return item


def _segment_item(fact: dict[str, Any], row: dict[str, Any], index: int) -> dict[str, Any] | None:
    text = " ".join(str(row.get("text") or "").split())[:SEGMENT_CHARS]
    if not text or not row.get("segment_id") or row.get("start_ms") is None:
        return None
    item = {
        "handle": f"F{index}",
        "source_type": "transcript_segment",
        "media_id": row.get("media_id"),
        "segment_id": row.get("segment_id"),
        "start_ms": row.get("start_ms"),
        "end_ms": row.get("end_ms"),
        "filename": row.get("filename"),
        "speaker": row.get("speaker"),
        "text": text,
    }
    if row.get("start_ms") is not None:
        item["start_mmss"] = text_terms.mmss(row["start_ms"])
    item.update(_fact_fields(fact))
    return item


def _citation_item(fact: dict[str, Any], row: dict[str, Any], index: int) -> dict[str, Any] | None:
    source = str(row.get("source_type") or "")
    if source == "transcript_segment" or (row.get("segment_id") and not row.get("document_id")):
        return _segment_item(fact, row, index)
    return _page_item(fact, row, index)


def _item_key(item: dict[str, Any]) -> tuple:
    if item.get("document_id") and item.get("page_number") is not None:
        return ("doc", str(item["document_id"]), int(item["page_number"]))
    return ("seg", str(item.get("segment_id")))


def _dated_media(conn: Connection, case_id: str, fact: dict[str, Any]) -> list[str]:
    """Medias cuyo nombre contiene la fecha del hecho en formato compacto."""
    proposition = str(fact.get("proposition") or "")
    found: list[str] = []
    for day, month, year in _DATE_IN_FACT.findall(proposition):
        token = f"{int(day)}{_MONTH_SHORT.get(month.lower(), month[:3].capitalize())}{year}"
        rows_ = rows(conn, """
            SELECT id::text AS id FROM media
             WHERE case_id = :c AND filename ILIKE :q
             ORDER BY id LIMIT 2
        """, c=case_id, q=f"%{token}%")
        for row in rows_:
            value = str(row.get("id") or "")
            if value and value not in found:
                found.append(value)
    return found


def _resolve(conn: Connection, case_id: str, fact: dict[str, Any],
             query: str) -> dict[str, Any] | None:
    """Resuelve UNA cita primaria del hecho o devuelve None (nunca inventa)."""
    fact_id = str(fact.get("id") or "")
    if not fact_id:
        return None
    terms = _fact_terms(fact)
    if len(terms) < MIN_FACT_TERMS_IN_SOURCE:
        return None
    # Si el hecho describe una diligencia cuya fecha está en el nombre del video,
    # la grabación es la fuente primaria (no la providencia que la resume). Se
    # comprueba ANTES de la cita registrada: la fecha debe sembrar el minuto
    # 37:08 de 0065, no solo la página de la providencia que lo relata.
    media_ids = _dated_media(conn, case_id, fact)
    if media_ids:
        segment = _best_segment(conn, case_id, query, terms, media_ids)
        if segment:
            return segment
    citation = _citation_by_target(conn, fact_id) or _citation_by_position(conn, fact_id)
    if citation:
        return citation
    documents, medias = _linked_sources(conn, fact_id)
    if documents:
        page = _best_page(conn, case_id, query, terms, documents)
        if page:
            return page
    if medias:
        segment = _best_segment(conn, case_id, query, terms, medias)
        if segment:
            return segment
    # La fuente vinculada no contiene el hecho con el umbral exigido (o no hay
    # fuente vinculada): se busca en el resto del expediente antes de descartarlo.
    page = _best_page(conn, case_id, query, terms, None)
    if page:
        return page
    return _best_segment(conn, case_id, query, terms, None)


def _legal_anchor(conn: Connection, case_id: str, index: int) -> dict[str, Any] | None:
    """Pasaje del expediente que menciona el artículo 886 (ancla normativa).

    Se consulta la fuente primaria (segmento o página), no el índice de chunks:
    así el texto que ve el modelo es el que el usuario podrá citar.
    """
    found = rows(conn, """
        SELECT ts.id::text AS segment_id, ts.media_id::text AS media_id,
               ts.start_ms, ts.end_ms, ts.text,
               m.filename, sp.display_name AS speaker
          FROM transcript_segments ts
          JOIN media m ON m.id = ts.media_id
          LEFT JOIN speakers sp ON sp.id = ts.speaker_id
         WHERE m.case_id = :c
           AND ts.text ILIKE '%886%'
           AND ts.text ILIKE '%Código de Comercio%'
         ORDER BY ts.start_ms, ts.id
         LIMIT 1
    """, c=case_id)
    if not found:
        found = rows(conn, """
            SELECT dp.document_id::text AS document_id, dp.page_number,
                   dp.text, d.filename
              FROM document_pages dp
              JOIN documents d ON d.id = dp.document_id
             WHERE d.case_id = :c
               AND dp.text ILIKE '%886%'
               AND dp.text ILIKE '%Código de Comercio%'
             ORDER BY dp.page_number
             LIMIT 1
        """, c=case_id)
    if not found:
        return None
    row = found[0]
    text = " ".join(str(row.get("text") or "").split())
    if not text:
        return None
    item: dict[str, Any] = {
        "handle": f"F{index}",
        "filename": row.get("filename"),
        "text": text[:SEGMENT_CHARS],
        "seed": "legal",
        "fact_label": "[Norma citada en la fuente: artículo 886]",
    }
    if row.get("segment_id") and row.get("start_ms") is not None:
        item.update({
            "source_type": "transcript_segment",
            "media_id": row.get("media_id"),
            "segment_id": row.get("segment_id"),
            "start_ms": row.get("start_ms"),
            "end_ms": row.get("end_ms"),
            "speaker": row.get("speaker"),
            "start_mmss": text_terms.mmss(row.get("start_ms")),
        })
    elif row.get("document_id") and row.get("page_number") is not None:
        item.update({
            "source_type": "document_page",
            "document_id": row.get("document_id"),
            "page_number": row.get("page_number"),
        })
    else:
        return None
    return item


_COUNT_WORDS = (r"\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|"
               r"doce|trece|catorce|quince|diecis[eé]is|diecisiete|dieciocho|"
               r"diecinueve|veinte")
_INVENTORY_OBJECTS = r"pagar[eé]s?|t[ií]tulos?\s+valores|obligaciones"
_MONTH_NAMES = (r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|"
                r"setiembre|octubre|noviembre|diciembre")
_DECLARATION_DATE = re.compile(
    r"\b(\d{1,2})\s+de\s+(" + _MONTH_NAMES + r")\s+(?:de\s+|del\s+)?(\d{4})\b",
    re.I,
)
_MONTH_SHORT = {
    "enero": "Ene", "febrero": "Feb", "marzo": "Mar", "abril": "Abr",
    "mayo": "May", "junio": "Jun", "julio": "Jul", "agosto": "Ago",
    "septiembre": "Sep", "setiembre": "Sep", "octubre": "Oct",
    "noviembre": "Nov", "diciembre": "Dic",
}


_CONTAINER_TERMS = {
    "interrogatorio", "interrogatorios", "declaracion", "declaraciones",
    "testimonio", "testimonios", "audiencia", "audiencias", "diligencia",
    "diligencias", "confesion",
}


def _is_container(term: str) -> bool:
    """Contenedor procesal, incluida la variante mal escrita que corrige el léxico."""
    return term in _CONTAINER_TERMS or bool(
        _CONTAINER_TERMS & set(text_terms.LEGAL_SYNONYMS.get(term, ())))


def _substantive_terms(question: str) -> list[str]:
    """Términos de fondo, sin las palabras de contenedor procesal.

    «interrogatorio», «audiencia» o «declaración» describen dónde buscar, no de
    qué se habla. Si entran al `to_tsquery`, el ranking premia la instalación de
    la audiencia (01:04) en vez de la declaración sobre intereses (37:08).
    """
    folded = text_terms.norm(question)
    terms = list(dict.fromkeys(
        text_terms.content_terms(question)
        + [term for variant in text_terms.expand_query(question)
           for term in text_terms.content_terms(variant)]))
    meta = {"busque", "busca", "revisa", "dice", "parte", "partes", "adicional",
            "documentacion", "hechos", "lleve", "determinar", "cobraron"}
    substantive = [term for term in terms
                   if not _is_container(term) and term not in meta]
    if _LEGAL_QUESTION.search(folded):
        substantive.extend(["pagares", "pagare", "vencidos", "capitalizacion",
                            "intereses", "usura", "deuda", "cobro", "anatocismo"])
        for key in ("pagare", "pagares", "vencidos", "capitalizacion", "intereses", "usura"):
            for synonym in text_terms.LEGAL_SYNONYMS.get(key, ()):
                for term in text_terms.content_terms(synonym):
                    if not _is_container(term):
                        substantive.append(term)
    return list(dict.fromkeys(substantive))


def _inventory_pattern(question: str) -> str:
    """Conteo cardinal o título numerado, sin números ni casos cableados."""
    folded = text_terms.norm(question)
    objects: list[str] = []
    if re.search(r"\bpagares?\b", folded):
        objects.append(r"pagar[eé]s?")
    if re.search(r"\btitulos?\b", folded):
        objects.append(r"t[ií]tulos?\s+valores")
    if re.search(r"\bobligaciones?\b", folded):
        objects.append("obligaciones")
    if not objects:
        objects.append(_INVENTORY_OBJECTS)
    objs = "(?:" + "|".join(objects) + ")"
    counted = r"\m(" + _COUNT_WORDS + r")[ ]+" + objs + r"\M"
    numbered = r"\m" + objs + r"[ ]+(?:n[°ºo.]?|n[uú]mero)[ ]*0*\d+\M"
    return r"(?:" + counted + r"|" + numbered + r")"


def _inventory_items(conn: Connection, case_id: str, question: str,
                     index: int) -> list[dict[str, Any]]:
    """Hasta 3 páginas de documentos distintos que cuantifican o enumeran títulos.

    No busca un número ni un archivo concreto: acepta conteos cardinales
    («quince pagarés», «4 obligaciones») y títulos numerados («Pagaré N° 001»),
    y elige la página con más seriales/montos de cada documento.
    """
    pattern = _inventory_pattern(question)
    pages = rows(conn, """
        SELECT dp.document_id::text AS document_id, dp.page_number, dp.folio,
               d.filename, dp.text,
               (SELECT count(*) FROM regexp_matches(dp.text, '\\d{5,}', 'g')) AS numeros
          FROM document_pages dp
          JOIN documents d ON d.id = dp.document_id
         WHERE d.case_id = :c AND dp.text ~* :pat
         ORDER BY numeros DESC, length(dp.text) DESC, dp.page_number
         LIMIT 50
    """, c=case_id, pat=pattern)
    def numbered_title(value: object) -> bool:
        return bool(re.search(r"pagar[eé]s?\s+(?:n[°ºo.]?|n[uú]mero)\s*0*\d+",
                              text_terms.norm(str(value or ""))))

    chosen: list[dict[str, Any]] = []
    docs: set[str] = set()

    def take(row: dict[str, Any]) -> None:
        doc_id = str(row.get("document_id") or "")
        if not doc_id or doc_id in docs or len(chosen) >= 3:
            return
        docs.add(doc_id)
        chosen.append(row)

    def count_token(value: object) -> str:
        # Solo conteos plausibles (1–2 dígitos o palabra): un serial de 8
        # dígitos no es «cuántos títulos hay».
        match = re.search(r"\b(\d{1,2}|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|"
                          r"diez|once|doce|trece|catorce|quince|diecis[eé]is|"
                          r"diecisiete|dieciocho|diecinueve|veinte)\s+"
                          r"(?:pagar|titulo|obligacion)",
                          text_terms.norm(str(value or "")))
        return match.group(1) if match else ""

    def title_number(value: object) -> int | None:
        match = re.search(r"pagar[eé]s?\s+(?:n[°ºo.]?|n[uú]mero)\s*0*(\d+)",
                          text_terms.norm(str(value or "")))
        return int(match.group(1)) if match else None

    if pages:
        take(pages[0])  # la de mayor cantidad de seriales/montos
    first_count = count_token(pages[0].get("text")) if pages else ""
    # Una página con OTRO conteo: el más pequeño (el otro grupo de títulos).
    counts = []
    for row in pages[1:]:
        token = count_token(row.get("text"))
        if token and token != first_count:
            try:
                value = int(token)
            except ValueError:
                value = 999
            counts.append((value, row))
    if counts:
        take(min(counts, key=lambda pair: pair[0])[1])
    # Una página con título numerado: la del número más bajo (título base).
    numbered = [(title_number(row.get("text")), row) for row in pages[1:]]
    numbered = [(number, row) for number, row in numbered if number is not None]
    if numbered:
        take(min(numbered, key=lambda pair: pair[0])[1])
    for row in pages[1:]:
        take(row)
    out: list[dict[str, Any]] = []
    for row in chosen:
        item = _page_from_row(row, index + len(out))
        if item is None:
            continue
        item["seed"] = "inventory"
        item["fact_label"] = "[Conteo o título individualizado en la fuente]"
        out.append(item)
    return out


def _segment_from_row(row: dict[str, Any], index: int,
                      label: str = "[Interrogatorio o audiencia]") -> dict[str, Any] | None:
    text = " ".join(str(row.get("text") or "").split())[:SEGMENT_CHARS]
    if not text or not row.get("segment_id") or row.get("start_ms") is None:
        return None
    return {
        "handle": f"F{index}", "source_type": "transcript_segment",
        "media_id": row.get("media_id"), "segment_id": row.get("segment_id"),
        "start_ms": row.get("start_ms"), "end_ms": row.get("end_ms"),
        "start_mmss": text_terms.mmss(row.get("start_ms")),
        "filename": row.get("filename"), "speaker": row.get("speaker"),
        "text": text, "seed": "testimony", "fact_label": label,
    }


def _page_from_row(row: dict[str, Any], index: int) -> dict[str, Any] | None:
    text = " ".join(str(row.get("text") or "").split())[:PAGE_CHARS]
    if not text or not row.get("document_id") or row.get("page_number") is None:
        return None
    return {
        "handle": f"F{index}", "source_type": "document_page",
        "document_id": row.get("document_id"), "page_number": row.get("page_number"),
        "folio": row.get("folio"), "filename": row.get("filename"), "text": text,
        "seed": "testimony", "fact_label": "[Declaración mencionada en la fuente]",
    }


def _declaration_items(conn: Connection, case_id: str, question: str,
                       index: int) -> list[dict[str, Any]]:
    """Segmentos de fondo primero; después las páginas que mencionan la declaración.

    El audio se rankea con términos sustantivos (sin «interrogatorio»/«audiencia»)
    y se agrega antes que las páginas para que `for_question` no lo recorte.
    """
    folded = text_terms.norm(" ".join(text_terms.expand_query(question)))
    if not re.search(r"\b(interrogatorio|interrogatorios|declaracion|declaraciones|"
                     r"testimonio|testimonios|audiencia|audiencias)\b", folded):
        return []
    all_terms = list(dict.fromkeys(
        text_terms.content_terms(question)
        + [term for variant in text_terms.expand_query(question)
           for term in text_terms.content_terms(variant)]))
    substantive = _substantive_terms(question)
    ranking_terms = substantive if len(substantive) >= MIN_FACT_TERMS_IN_SOURCE else all_terms
    substantive_query = or_query(ranking_terms)
    page_query = or_query(all_terms)
    if not substantive_query or not page_query:
        return []
    pages = rows(conn, """
        SELECT dp.document_id::text AS document_id, dp.page_number, dp.folio,
               d.filename, dp.text
          FROM document_pages dp
          JOIN documents d ON d.id = dp.document_id
         WHERE d.case_id = :c
           AND dp.text ~* '(interrogatorio|declaraci[oó]n|testimonio)'
         ORDER BY ts_rank(to_tsvector('spanish', immutable_unaccent(dp.text)),
                          plainto_tsquery('spanish', :q)) DESC,
                  length(dp.text) DESC, dp.page_number
         LIMIT 20
    """, c=case_id, q=" ".join(all_terms))
    audio: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for page in pages:
        for day, month, year in _DECLARATION_DATE.findall(str(page.get("text") or "")):
            token = f"{int(day)}{_MONTH_SHORT.get(month.lower(), month[:3].capitalize())}{year}"
            media = rows(conn, """
                SELECT id::text AS id FROM media
                 WHERE case_id = :c AND filename ILIKE :q LIMIT 2
            """, c=case_id, q=f"%{token}%")
            for media_row in media:
                found = rows(conn, """
                    SELECT s.id::text AS segment_id, s.media_id::text AS media_id,
                           s.start_ms, s.end_ms, s.text, m.filename, sp.display_name AS speaker,
                           ts_rank_cd(s.search_tsv, to_tsquery('spanish', :q)) AS rank
                      FROM transcript_segments s
                      JOIN media m ON m.id = s.media_id
                      LEFT JOIN speakers sp ON sp.id = s.speaker_id
                     WHERE s.media_id = :m
                       AND s.search_tsv @@ to_tsquery('spanish', :q)
                     ORDER BY rank DESC, s.start_ms
                     LIMIT 20
                """, m=media_row["id"], q=substantive_query)
                for segment in found:
                    if not _accepts(str(segment.get("text") or ""), ranking_terms):
                        continue
                    seg_item = _segment_from_row(segment, index + len(audio))
                    if seg_item:
                        key = _item_key(seg_item)
                        if key not in seen:
                            seen.add(key)
                            audio.append(seg_item)
    segment = _best_segment(conn, case_id, substantive_query, ranking_terms, None)
    seg_item = _segment_from_row(segment, index + len(audio)) if segment else None
    if seg_item:
        key = _item_key(seg_item)
        if key not in seen:
            audio.append(seg_item)
    # Audio primero, ordenado por densidad de términos sustantivos: así la
    # declaración de fondo (p. ej. «pagarés… intereses vencidos…») queda antes
    # que la instalación de la audiencia.
    def _audio_score(item: dict[str, Any]) -> tuple[int, int]:
        text = text_terms.norm(str(item.get("text") or ""))
        density = sum(1 for term in ranking_terms if term and term in text)
        return (-density, int(item.get("start_ms") or 0))

    audio.sort(key=_audio_score)
    out: list[dict[str, Any]] = list(audio[:MAX_AUDIO_SEEDS])
    for offset, item in enumerate(out, start=index):
        item["handle"] = f"F{offset}"
    pages_added = 0
    for page in pages:
        page_item = _page_from_row(page, index + len(out))
        if page_item is None:
            continue
        key = _item_key(page_item)
        if key in seen:
            continue
        seen.add(key)
        pages_added += 1
        out.append(page_item)
        if pages_added >= 4:
            break
    return out


def for_question(conn: Connection, case_id: str, question: str,
                 limit: int = 6) -> list[dict[str, Any]]:
    """Hechos del caso que responden a la pregunta, con su página o segmento."""
    limit = max(0, int(limit or 0))
    if not limit:
        return []
    terms = text_terms.content_terms(question)
    if len(set(terms)) < MIN_TERMS:
        return []
    query = or_query(terms)
    if not query:
        return []
    candidates = _candidates(conn, case_id, query, limit * 3)
    if not candidates:
        return []
    # Relevancia primero (los candidatos ya vienen por rank) y, dentro de ellos,
    # estado epistémico: probado/admitido → sostenido por evidencia → resto.
    # `ALLEGED`/`DISPUTED` no se excluyen: muchas preguntas se responden con la
    # alegación de una parte, siempre que la cita lo respalde.
    ordered = sorted(candidates, key=_priority)
    items: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for fact in ordered:
        resolved = _resolve(conn, case_id, fact, query)
        if not resolved:
            continue
        item = _citation_item(fact, resolved, len(items) + 1)
        if item is None:
            continue
        key = _item_key(item)
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        if len(items) >= limit:
            break
    if _LEGAL_QUESTION.search(text_terms.norm(question)) and len(items) < limit:
        anchor = _legal_anchor(conn, case_id, len(items) + 1)
        if anchor is not None:
            key = _item_key(anchor)
            if key not in seen:
                items.append(anchor)
    folded = text_terms.norm(question)
    # El audio sustantivo entra antes que las páginas y antes del inventario:
    # así `for_question` no lo recorta al llenar el cupo.
    for extra in _declaration_items(conn, case_id, question, len(items) + 1):
        key = _item_key(extra)
        if key in seen:
            continue
        seen.add(key)
        items.append(extra)
        if len(items) >= limit + 12:
            break
    if limit >= 7 and (_LEGAL_QUESTION.search(folded) or _INVENTORY_QUESTION.search(folded)):
        for extra in _inventory_items(conn, case_id, question, len(items) + 1):
            key = _item_key(extra)
            if key in seen:
                continue
            seen.add(key)
            items.append(extra)
            if len(items) >= limit + 12:
                break
    # Handles densos y únicos: los grupos internos numeran antes de que
    # `for_question` descarte duplicados o recorte el cupo.
    for offset, item in enumerate(items, start=1):
        item["handle"] = f"F{offset}"
    return items


__all__ = ["for_question", "or_query"]
