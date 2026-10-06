"""Tools de LECTURA del caso: OCR, ASR, pgvector (híbrido), archivos y grafo.

Todas acotan por `case_id` (y la conexión ya viene con RLS por organización),
de modo que un id de otro expediente u otra org simplemente no devuelve filas.
"""
from __future__ import annotations

import uuid as _uuid_mod
import re
from collections import Counter
from typing import Any

from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows
from app.services import answering, graph, markdown
from app.services.case_tools import ToolContext, evidence_item, register


def _uuid(value: Any) -> str | None:
    try:
        return str(_uuid_mod.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _err(msg: str) -> list[dict[str, Any]]:
    return [{"handle": "ERR", "source_type": "error", "text": msg}]


def mmss(ms: int | None) -> str:
    """842000 ms -> '14:02' (para '¿en qué minuto se dijo?')."""
    if ms is None:
        return ""
    total = int(ms) // 1000
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _ilike_terms(query: str, column: str = "text") -> tuple[str, dict[str, Any]]:
    terms = [t for t in query.lower().split() if len(t) >= 3]
    if not terms:
        return "", {}
    clauses, params = [], {}
    for i, term in enumerate(terms):
        key = f"t{i}"
        clauses.append(f"{column} ILIKE :{key}")
        params[key] = f"%{term}%"
    return " AND ".join(clauses), params


# Palabras de la pregunta que NO son el nombre buscado (para aislar "Paola" en
# "¿en qué minuto habló la doctora Paola?").
_QUESTION_WORDS = {
    "que", "qué", "quien", "quién", "quienes", "quiénes", "en", "el", "la", "los", "las", "un", "una",
    "del", "de", "al", "video", "vídeo", "minuto", "minutos", "segundo", "segundos", "hablo", "habló",
    "hablaron", "habla", "hablar", "dijo", "dijeron", "menciono", "mencionó", "mencionaron", "sobre", "cual", "cuál",
    "donde", "dónde", "cuando", "cuándo", "doctor", "doctora", "señor", "señora", "senor", "senora",
    "parte", "audio", "grabacion", "grabación", "audiencia", "transcripcion", "transcripción", "por", "para",
    # verbos/palabras de pregunta que no son el término buscado
    "me", "te", "puedes", "podrias", "podrías", "decir", "dime", "cuentame", "cuéntame", "resume", "resumen",
    "es", "son", "fue", "fueron", "ser", "hay", "tiene", "tienen", "hacer", "hacen", "explica", "explicar",
    "conocer", "sabes", "saber", "dame", "muestra", "muestrame", "muéstrame", "informacion", "información",
    "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas", "cuales", "cuáles", "tales",
    # verbos genéricos de la pregunta (no son el término buscado)
    "hace", "realiza", "realizo", "realizó", "realizaron", "presenta", "presento", "presentó",
    "presentaron", "solicita", "solicito", "solicitó", "tramita", "existe", "existen", "contiene", "figura",
    "trata", "consta", "obra", "ver", "listar", "lista", "encontrar",
    # sustantivos de "dónde/archivo": no son el término buscado
    "documento", "documentos", "archivo", "archivos", "expediente", "carpeta",
    "pagina", "página", "paginas", "páginas", "folio", "folios", "anexo", "anexos",
}


def _speaker_candidate(query: str) -> str | None:
    """Extrae un posible nombre propio/sustantivo de la consulta; None si no hay."""
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", query or "")
    cands = [t for t in tokens if t.lower() not in _QUESTION_WORDS]
    if not cands:
        return None
    proper = [t for t in cands if t[:1].isupper()]
    return max(proper, key=len) if proper else max(cands, key=len)


def _tsquery(conn: Connection, query: str) -> str | None:
    """Lexemas del caso (FTS_CONFIG) unidos con OR, citados de forma segura."""
    cfg = get_settings().FTS_CONFIG
    return rows(conn, "SELECT string_agg(quote_literal(lexeme), ' | ') AS q FROM unnest(to_tsvector(CAST(:cfg AS regconfig), :t)) "
                      "WHERE length(lexeme) >= 3", cfg=cfg, t=query)[0]["q"]


# ---------------------------------------------------------------------------
# Búsqueda híbrida (FTS + pgvector) acotable por los "@" del chat
# ---------------------------------------------------------------------------

def _fold_py(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


_FOLD_SQL = "translate(lower({col}), 'áéíóúüñ', 'aeiouun')"


def _phrase_pattern(term: str) -> str | None:
    """Patrón de FRASE: tokens en orden con hasta 8 caracteres no alfanuméricos entre ellos
    («pagaré 001» -> \\mpagare[^a-z0-9]{0,8}0*1\\M, que halla «PAGARE No. 01»)."""
    toks = [t for t in re.split(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", term or "") if t]
    if not toks:
        return None
    parts = []
    for t in toks:
        if t.isdigit():
            d = t.lstrip("0")
            parts.append("0*" + (d if d else "0"))
        else:
            parts.append(re.escape(_fold_py(t)))
    return r"\m" + r".{0,12}".join(parts) + r"\M"


def _exact_matches(conn: Connection, case_id: str, term: str, k: int) -> list[dict[str, Any]]:
    """Páginas y segmentos que contienen la FRASE buscada (insensible a tildes/mayúsculas),
    ordenadas por longitud (las páginas de un título valor son cortas y directas)."""
    pat = _phrase_pattern(term)
    if not pat:
        return []
    params = {"c": case_id, "pat": pat, "k": k}
    out = _page_items(conn, case_id, "document_pages", "p",
                      [f"{_FOLD_SQL.format(col='p.text')} ~ :pat"], params, k, True, "'pagina'", order="file")
    out += _page_items(conn, case_id, "document_ocr_versions", "v",
                       [f"{_FOLD_SQL.format(col='v.text')} ~ :pat"], params, k, False, "v.mode", order="file")
    out = _dedupe_pages(out)
    segs = rows(conn, """
        SELECT s.id, s.media_id, s.start_ms, s.end_ms, sp.label AS speaker, sp.display_name AS speaker_name,
               left(s.text, 240) AS text, m.filename
        FROM transcript_segments s JOIN media m ON m.id = s.media_id
        LEFT JOIN speakers sp ON sp.id = s.speaker_id
        WHERE m.case_id = :c AND /*FOLD*/ ~ :pat
        ORDER BY m.filename, s.start_ms LIMIT :k""".replace("/*FOLD*/", _FOLD_SQL.format(col="s.text")), **params)
    out += [_segment_item(r, prefix="TR") for r in segs]
    return out[:k]


def _content_term(query: str) -> str | None:
    """Términos de contenido de la pregunta (sin palabras vacías), p. ej.
    «¿Qué me puedes decir del pagaré 001?» -> «pagaré 001». None si no queda nada útil."""
    tokens = re.findall(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}", query or "")
    keep = [t for t in tokens if t.lower() not in _QUESTION_WORDS and len(t) >= 2]
    return " ".join(keep[:4]).strip() or None


@register("search_case",
          "Búsqueda híbrida (texto completo + vectores pgvector) en TODO el expediente: "
          "páginas OCR y transcripciones. Puede acotarse a archivos adjuntos con document_ids/media_ids.",
          {"query": {"type": "string", "description": "Pregunta o términos en lenguaje natural"},
           "k": {"type": "integer", "default": 8, "description": "Máximo de resultados"},
           "document_ids": {"type": "array", "items": {"type": "string"}, "description": "Opcional: limitar a estos documentos"},
           "media_ids": {"type": "array", "items": {"type": "string"}, "description": "Opcional: limitar a estos audios/videos"}})
def search_case(conn: Connection, case_id: str, ctx: ToolContext, query: str, k: int = 8,
                document_ids: list[str] | None = None, media_ids: list[str] | None = None) -> list[dict[str, Any]]:
    doc_ids = [u for u in (_uuid(x) for x in (document_ids or [])) if u] or None
    med_ids = [u for u in (_uuid(x) for x in (media_ids or [])) if u] or None
    items = answering.retrieve(conn, case_id, query, document_ids=doc_ids, media_ids=med_ids)
    # Búsqueda AMPLIA (todas las fuentes): además del híbrido, cubre
    #  (1) transcripciones por TEXTO y por NOMBRE DE HABLANTE, y
    #  (2) coincidencias EXACTAS del término (insensible a tildes/mayúsculas), para que
    #      entre la página que literalmente contiene lo buscado (p. ej. «PAGARE No. 001»).
    if not doc_ids and not med_ids:
        tr: list[dict[str, Any]] = []
        try:
            tr = search_transcript_by_time(conn, case_id, ctx, query=query, k=k)[: max(1, k // 2)]
        except Exception:  # noqa: BLE001
            tr = []
        exact: list[dict[str, Any]] = []
        term = _content_term(query)
        if term:
            try:
                exact = _exact_matches(conn, case_id, term, k)
            except Exception:  # noqa: BLE001
                exact = []
        if exact or tr:
            items = answering.merge_items(exact, answering.merge_items(tr, items))
    return items[:k] if k else items


@register("search_documents",
          "Búsqueda léxica (FTS) en documentos OCRizados y transcripciones. "
          "Preferir search_case salvo que se necesite coincidencia literal.",
          {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}})
def search_documents(conn: Connection, case_id: str, ctx: ToolContext, query: str, k: int = 5) -> list[dict[str, Any]]:
    items = answering.legacy_retrieve(conn, case_id, query)
    return items[:k] if k else items


# ---------------------------------------------------------------------------
# Documentos (OCR)
# ---------------------------------------------------------------------------

def _resolve_document(conn: Connection, case_id: str, value: Any) -> str | None:
    """Acepta un id (UUID) o el NOMBRE del archivo (el modelo suele pasar el filename)."""
    v = _uuid(value)
    if v:
        return v
    text = str(value or "").strip()
    if len(text) < 3:
        return None
    r = rows(conn, "SELECT id FROM documents WHERE case_id = :c AND filename ILIKE :q "
                   "ORDER BY length(filename) LIMIT 1", c=case_id, q=f"%{text[:200]}%")
    if not r:
        base = text.split(".")[0][:150]
        r = rows(conn, "SELECT id FROM documents WHERE case_id = :c AND filename ILIKE :q LIMIT 1",
                 c=case_id, q=f"%{base}%")
    return str(r[0]["id"]) if r else None


@register("read_document",
          "Lee un documento completo o un rango de páginas (máx. 10 por llamada). "
          "Úsalo cuando el usuario adjunta un archivo (@) y pregunta qué dice.",
          {"document_id": {"type": "string", "format": "uuid"},
           "from_page": {"type": "integer", "default": 1},
           "to_page": {"type": "integer", "description": "Opcional; por defecto from_page+9"}})
def read_document(conn: Connection, case_id: str, ctx: ToolContext, document_id: str,
                  from_page: int = 1, to_page: int | None = None) -> list[dict[str, Any]]:
    doc_id = _resolve_document(conn, case_id, document_id)
    if not doc_id:
        return _err("Documento no encontrado (usa el id o el nombre exacto del archivo)")
    doc = rows(conn, "SELECT filename, page_count FROM documents WHERE id = :d AND case_id = :c", d=doc_id, c=case_id)
    if not doc:
        return _err("Documento no encontrado en este expediente")
    from_page = max(1, int(from_page or 1))
    to_page = min(int(to_page) if to_page else from_page + 9, from_page + 9)
    pages = rows(conn, """
        SELECT p.page_number, p.folio, left(p.text, 4000) AS text, p.ocr_confidence, p.needs_review
        FROM document_pages p WHERE p.document_id = :d AND p.page_number BETWEEN :a AND :b
        ORDER BY p.page_number""", d=doc_id, a=from_page, b=to_page)
    if not pages:
        return _err(f"El documento {doc[0]['filename']} no tiene páginas OCRizadas en el rango {from_page}-{to_page}")
    return [evidence_item("P", "document_page", pg["text"], document_id=doc_id, page_number=pg["page_number"],
                          folio=pg["folio"], filename=doc[0]["filename"],
                          ocr_confidence=pg["ocr_confidence"], needs_review=pg["needs_review"]) for pg in pages]


@register("get_document_page",
          "Obtiene el texto OCR completo de una página específica de un documento.",
          {"document_id": {"type": "string", "format": "uuid"},
           "page_number": {"type": "integer"}})
def get_document_page(conn: Connection, case_id: str, ctx: ToolContext, document_id: str,
                      page_number: int) -> list[dict[str, Any]]:
    doc_id = _resolve_document(conn, case_id, document_id)
    if not doc_id:
        return _err("Documento no encontrado (usa el id o el nombre exacto del archivo)")
    r = rows(conn, """
        SELECT p.text, p.folio, d.filename FROM document_pages p
        JOIN documents d ON d.id = p.document_id
        WHERE p.document_id = :d AND p.page_number = :n AND d.case_id = :c
    """, d=doc_id, n=page_number, c=case_id)
    if not r:
        return _err("Página no encontrada")
    return [evidence_item("P", "document_page", r[0]["text"], document_id=doc_id,
                          page_number=page_number, folio=r[0]["folio"], filename=r[0]["filename"])]


@register("get_document_markdown",
          "Devuelve el documento OCR completo YA ESTRUCTURADO en Markdown (títulos, listas y secciones "
          "por página). Úsalo para LEER y comprender un documento largo de una vez; para CITAR una página "
          "concreta usa read_document/get_document_page.",
          {"document_id": {"type": "string", "format": "uuid",
                           "description": "id del documento o el nombre del archivo"}})
def get_document_markdown(conn: Connection, case_id: str, ctx: ToolContext, document_id: str) -> list[dict[str, Any]]:
    doc_id = _resolve_document(conn, case_id, document_id)
    if not doc_id:
        return _err("Documento no encontrado (usa el id o el nombre exacto del archivo)")
    md = markdown.document_markdown(conn, case_id, doc_id)
    if not md:
        return _err("El documento no tiene texto OCR para convertir a Markdown")
    fn = rows(conn, "SELECT filename FROM documents WHERE id = :d AND case_id = :c", d=doc_id, c=case_id)
    return [evidence_item("MD", "document_markdown", md, document_id=doc_id,
                          filename=fn[0]["filename"] if fn else None)]


# ---------------------------------------------------------------------------
# Transcripciones (ASR) y búsqueda por minuto
# ---------------------------------------------------------------------------

def _segment_item(r: dict[str, Any], prefix: str = "TR") -> dict[str, Any]:
    speaker = r.get("speaker_name") or r.get("speaker")
    return evidence_item(prefix, "transcript_segment", r["text"], media_id=str(r["media_id"]),
                         segment_id=str(r["id"]), start_ms=r["start_ms"], end_ms=r["end_ms"],
                         start_mmss=mmss(r["start_ms"]), end_mmss=mmss(r["end_ms"]),
                         speaker=speaker, filename=r["filename"])


@register("list_speakers",
          "Lista los hablantes (diarización) del expediente: speaker_id, etiqueta, nombre visible, rol, "
          "estado de resolución y cuántos segmentos habla. Úsalo para resolver quién es quién antes de "
          "renombrar un hablante o responder '¿cómo se llama el juez / la apoderada?'.",
          {})
def list_speakers(conn: Connection, case_id: str, ctx: ToolContext) -> list[dict[str, Any]]:
    sps = rows(conn, """
        SELECT sp.id, sp.label, sp.display_name, sp.speaker_role, sp.resolution_status,
               (SELECT count(*) FROM transcript_segments t WHERE t.speaker_id = sp.id) AS segments,
               (SELECT count(DISTINCT t.media_id) FROM transcript_segments t WHERE t.speaker_id = sp.id) AS media_count
        FROM speakers sp WHERE sp.case_id = :c
        ORDER BY segments DESC, sp.label
    """, c=case_id)
    return [evidence_item("SPK", "speaker", sp["display_name"] or sp["label"],
                          speaker_id=str(sp["id"]), label=sp["label"], display_name=sp["display_name"],
                          speaker_role=sp["speaker_role"], resolution_status=sp["resolution_status"],
                          segments=sp["segments"], media_count=sp["media_count"]) for sp in sps]


# ---------------------------------------------------------------------------
# Personas por rol (preguntas de agregación: "¿cuántos jueces han intervenido?")
# ---------------------------------------------------------------------------
_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "juez": ("juez", "jueza"),
    "apoderado": ("apoderado", "apoderada", "abogado", "abogada"),
    "testigo": ("testigo",),
    "perito": ("perito",),
    "secretario": ("secretario", "secretaria"),
    "fiscal": ("fiscal",),
    "parte": ("demandante", "demandado"),
}

# Patrones para el ROL CONFIRMADO por el usuario en speakers.speaker_role (español o inglés).
_ROLE_CONFIRMED: dict[str, tuple[str, ...]] = {
    "juez": ("juez", "jueza", "judge", "magistrad"),
    "apoderado": ("apoderad", "abogad", "attorney"),
    "testigo": ("testig", "witness"),
    "perito": ("perit", "expert"),
    "secretario": ("secretari", "clerk"),
    "fiscal": ("fiscal",),
    "parte": ("parte", "party", "demandante", "demandado"),
}

# Palabras que NO son nombres de persona (roles, cargos, números en letras, etc.).
_STOP_NAME_TOKENS = frozenset("""
JUEZ JUEZA JUZGADO CIRCUITO BOGOTA BOGOTÁ COLOMBIA REPUBLICA REPÚBLICA CIVIL TRIBUNAL PENAL LABORAL
FAMILIA SALA CORTE SUPREMA CONSEJO ESTADO SUPERIOR JUDICATURA ADMINISTRATIVO SECRETARIO SECRETARIA
NOTARIA NOTARIAL PROCESO PROCESAL EJECUTIVO EJECUTIVA AUTO AUTOS SEÑOR SEÑORA SENOR DOCTOR DOCTORA
ABOGADO ABOGADA APODERADO APODERADA DEMANDA DEMANDADO DEMANDANTE RECURSO APELACION APELACIÓN SENTENCIA
PROVIDENCIA RESUELVE CONSTANCIA PODER HOJA RADICADO EXPEDIENTE HONORABLE ATENCION ATENTAMENTE CORDIAL
ASUNTO REF MAIL CORREO OUTLOOK INFORMATIVO INFORME SECRETARIAL RESUMEN VERSION VERSIÓN FECHA FIRMA
FIRMADO COMO DEL POR PARA CON ENTRE LOS LAS UNO UNA UNOS UNAS DOS TRES CUATRO CINCO SEIS SIETE OCHO
NUEVE DIEZ ONCE DOCE QUINCE VEINTE TREINTA CUARENTA CINCUENTA SESENTA SETENTA OCHENTA NOVENTA CIENTO
DOSCIENTOS TRESCIENTOS CUATROCIENTOS QUINIENTOS SEISCIENTOS SETECIENTOS OCHOCIENTOS NOVECIENTOS MIL
MILLONES BILLONES PESOS PESO CENTAVOS CENTAVO DOLAR DÓLAR DOLARES DÓLARES MCTE INTERESES CAPITAL
QUIROGRAFARIO DENTRO ACUMULADO GARANTIA GARANTÍA REAL EFECTIVIDAD
HACE SABER TIPO LEY APLICA LINK SOLICITUD RESPETADA RESPETADO RESPETABLE SRA DRA CIU CTO BOG SIENDO
TITULADA TITULADO DERECHO DERECHOS ABOGACIA ESPECIALISTA ESPECIALISTAS MAESTRIA MAESTRÍA
""".split())

_NAME_RE = re.compile(
    r"\b([A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑa-záéíóúüñ]{1,}(?:\s+[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑa-záéíóúüñ]{1,}){1,3})\b"
)


def _clean_name(raw: str) -> str | None:
    """Normaliza un candidato a nombre: 2-4 tokens alfabéticos que no sean cargos/números."""
    toks = [t for t in (raw or "").split()
            if len(t) >= 3 and t.isalpha() and t.upper() not in _STOP_NAME_TOKENS]
    return " ".join(toks) if 2 <= len(toks) <= 4 else None


def _name_candidates(text: str) -> list[str]:
    """Secuencias de 2-4 palabras tipo nombre propio (MAYÚSCULAS o Capitalizadas),
    descartando cargos, números en letras y palabras no alfabéticas."""
    return [n for m in _NAME_RE.findall(text or "") if (n := _clean_name(m))]


def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _name_tokens(name: str) -> set[str]:
    """Tokens significativos del nombre (≥3 letras, sin acentos, en mayúsculas) para agrupar variantes OCR."""
    return {t for t in re.split(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", _strip_accents(name).upper()) if len(t) >= 3}


def _concat_norm(name: str) -> str:
    return re.sub(r"[^A-Z]", "", _strip_accents(name or "").upper())


def _fuzzy_same(a: str, b: str) -> bool:
    """Dos nombres son la misma persona pese a OCR: 'ALBALUCY COCKKEVAREZ' ≈ 'ALBA LUCY COCK ALVAREZ'."""
    ca, cb = _concat_norm(a), _concat_norm(b)
    if min(len(ca), len(cb)) < 8:
        return False
    if ca in cb or cb in ca:
        return True
    from difflib import SequenceMatcher
    return SequenceMatcher(None, ca, cb).ratio() >= 0.88


def _role_signature_matches(text: str, keywords: tuple[str, ...]) -> list[tuple[str, str]]:
    """Pares (nombre, fragmento) en la FIRMA (nombre seguido del cargo, p. ej. 'JUAN PEREZ JUEZ').

    Se usa sólo el patrón nombre→cargo: es el de las firmas de autos. El patrón
    inverso ('SEÑOR JUEZ' + destinatario) traía a las partes del encabezado como ruido."""
    out: list[tuple[str, str]] = []
    txt = text or ""
    for kw in keywords:
        pat = _NAME_RE.pattern + r"[ \t]*[,;.\-]?[ \t]*\n?[ \t]*(?i:" + re.escape(kw) + r")\b"
        for m in re.finditer(pat, txt):
            name = _clean_name(m.group(1))
            if not name:
                continue
            snippet = re.sub(r"\s+", " ", txt[max(0, m.start() - 120): m.end() + 120]).strip()
            out.append((name, snippet))
    return out



@register("list_people_by_role",
          "Lista CANDIDATOS de personas por rol (juez, apoderado, testigo, perito, secretario, parte) leídos "
          "de las firmas/encabezados de los documentos, con el número de menciones y su cita. Úsalo para "
          "preguntas de agregación como '¿cuántos jueces han intervenido en el proceso?'. Es heurístico y "
          "puede traer ruido: cita y verifica antes de concluir; no es una lista oficial.",
          {"role": {"type": "string", "description": "juez, apoderado, testigo, perito, secretario o parte"},
           "k": {"type": "integer", "default": 15, "description": "Máximo de nombres a devolver"}})
def list_people_by_role(conn: Connection, case_id: str, ctx: ToolContext, role: str,
                        k: int = 15) -> list[dict[str, Any]]:
    role_key = (role or "").strip().lower()
    keywords = _ROLE_KEYWORDS.get(role_key) or _ROLE_KEYWORDS.get(role_key.rstrip("s"))
    if not keywords:
        return _err("rol no soportado; usa uno de: " + ", ".join(sorted(_ROLE_KEYWORDS)))
    cpats = "{" + ",".join(f"%{kw}%" for kw in _ROLE_CONFIRMED.get(role_key, (role_key,))) + "}"

    # 1) Roles CONFIRMADOS por el usuario (speakers.speaker_role) → conteo EXACTO (no heurístico).
    confirmed = rows(conn, """
        SELECT sp.id, sp.label, sp.display_name, sp.speaker_role, sp.resolved_party_id, pt.name AS party_name,
               (SELECT count(*) FROM transcript_segments t WHERE t.speaker_id = sp.id) AS segments,
               seg.media_id, seg.start_ms, seg.segment_id
        FROM speakers sp
        LEFT JOIN parties pt ON pt.id = sp.resolved_party_id
        LEFT JOIN LATERAL (
            SELECT t.media_id, t.start_ms, t.id AS segment_id FROM transcript_segments t
            WHERE t.speaker_id = sp.id ORDER BY t.start_ms LIMIT 1) seg ON true
        WHERE sp.case_id = :c AND sp.speaker_role IS NOT NULL AND btrim(sp.speaker_role) <> ''
          AND lower(sp.speaker_role) LIKE ANY(CAST(:pats AS text[]))
        ORDER BY segments DESC, sp.label
    """, c=case_id, pats=cpats)
    if confirmed:
        # source_type transcript_segment cuando hay un segmento representativo: así el
        # conteo por rol confirmado es evidencia PRIMARIA (citable) y no una nota.
        items: list[dict[str, Any]] = []
        for sp in confirmed[:k]:
            name = sp["display_name"] or sp["label"]
            items.append(evidence_item(
                "PER", "transcript_segment" if sp["media_id"] else "speaker",
                f"{name}"
                + (f" — {sp['speaker_role']}" if sp["speaker_role"] else "")
                + (f" ({sp['party_name']})" if sp["party_name"] else ""),
                speaker_id=str(sp["id"]), label=sp["label"], display_name=sp["display_name"],
                person_name=name, role=role_key, speaker_role=sp["speaker_role"], segments=sp["segments"],
                confirmed=True, party_name=sp["party_name"],
                media_id=sp["media_id"], start_ms=sp["start_ms"], segment_id=sp["segment_id"],
                resolved_party_id=str(sp["resolved_party_id"]) if sp["resolved_party_id"] else None))
        return items

    # 2) Roles INSTITUCIONALES (juez/fiscal/secretario): sus "personas" reales son las
    #    AUTORIDADES judiciales que actuaron (juzgados, tribunales, salas), no las firmas.
    if role_key in _AUTHORITY_ROLES:
        auth = _authority_candidates(conn, case_id, role_key, k)
        if auth:
            return auth

    # 3) Respaldo HEURÍSTICO (firmas/encabezados) si aún no hay roles confirmados.
    return _role_candidates_heuristic(conn, case_id, role_key, keywords, k)


# --- Autoridades judiciales (juez/fiscal/secretario) ---
_AUTHORITY_ROLES: set[str] = set()  # rol «juez» → PERSONAS (nombres), no despachos; ver nota arriba
_AUTHORITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "juez": ("juez", "jueza", "juzgado", "tribunal", "corte", "magistrad", "sala"),
    "fiscal": ("fiscal", "fiscalia"),
    "secretario": ("secretaria",),
}
_NUM_WORDS = {
    "PRIMERO": "1", "PRIMER": "1", "SEGUNDO": "2", "TERCERO": "3", "CUARTO": "4", "QUINTO": "5",
    "SEXTO": "6", "SEPTIMO": "7", "OCTAVO": "8", "NOVENO": "9", "DECIMO": "10", "ONCE": "11",
    "DOCE": "12", "TRECE": "13", "CATORCE": "14", "QUINCE": "15", "DIECISEIS": "16", "DIECISIETE": "17",
    "DIECIOCHO": "18", "DIECINUEVE": "19", "VEINTE": "20", "VEINTIUNO": "21", "VEINTIUN": "21",
    "VEINTIDOS": "22", "VEINTITRES": "23", "VEINTICUATRO": "24", "VEINTICINCO": "25",
    "VEINTISEIS": "26", "VEINTISIETE": "27", "VEINTIOCHO": "28", "VEINTINUEVE": "29", "TREINTA": "30",
    "TREINTAIDOS": "32", "TREINTAYTRES": "33", "CUARENTA": "40",
}
_AUTHORITY_DROP = {"DE", "DEL", "LA", "EL", "LOS", "LAS", "D", "C", "DC", "NO", "N", "NUMERO"}
_CLASS_TOKENS = {"CIVIL", "MUNICIPAL", "CIRCUITO", "EJECUCION", "SENTENCIAS", "LABORAL", "FAMILIA",
                 "PENAL", "PROMISCUO", "PEQUENAS", "CAUSAS", "COMPETENCIA", "MULTIPLE", "CONSTITUCIONAL",
                 "CASACION", "SUPERIOR", "ADMINISTRATIVO", "JUSTICIA", "SUPREMA", "REPARTO"}


_TENS = {"VEINTE": 20, "TREINTA": 30, "CUARENTA": 40, "CINCUENTA": 50, "SESENTA": 60,
         "SETENTA": 70, "OCHENTA": 80, "NOVENTA": 90}
_UNITS = {"UNO": 1, "DOS": 2, "TRES": 3, "CUATRO": 4, "CINCO": 5, "SEIS": 6, "SIETE": 7,
          "OCHO": 8, "NUEVE": 9}


def _authority_signature(name: str) -> str:
    """Firma canónica de una autoridad para agrupar variantes OCR: tipo + número + clase.

    'Juzgado 21 Civil del Circuito de Bogotá' = 'Juzgado Veintiuno (21) Civil del
    Circuito … D.C.' = 'JUZGADO 021 Civil del Circuito' → misma firma.
    """
    raw: list[str] = []
    for t in re.split(r"[^A-Z0-9]+", _strip_accents(name or "").upper()):
        if t and t not in _AUTHORITY_DROP:
            raw.append(t)
    # Combina decenas + unidades ANTES de mapear a dígitos: "CUARENTA","Y","CINCO" → "45".
    merged: list[str] = []
    i = 0
    while i < len(raw):
        base = _TENS.get(raw[i])
        if base and i + 2 < len(raw) and raw[i + 1] in ("Y", "I"):
            unit = _UNITS.get(raw[i + 2])
            if unit:
                merged.append(str(base + unit))
                i += 3
                continue
        merged.append(raw[i])
        i += 1
    toks: list[str] = []
    for t in merged:
        t = _NUM_WORDS.get(t, t)
        if t.isdigit():
            t = str(int(t))
        toks.append(t)
    if any(t in ("JUZGADO", "JUEZ", "JUEZA", "JUECES") for t in toks):
        kind = "JUZGADO"
    elif "TRIBUNAL" in toks:
        kind = "TRIBUNAL"
    elif "CORTE" in toks:
        kind = "CORTE"
    elif any(t.startswith("FISCAL") for t in toks):
        kind = "FISCALIA"
    elif any(t.startswith("SECRETAR") for t in toks):
        kind = "SECRETARIA"
    else:
        kind = "OTRO"
    number = next((t for t in toks if t.isdigit()), "")
    classes = sorted({t for t in toks if t in _CLASS_TOKENS})
    return f"{kind}|{number}|{','.join(classes)}"


def _authority_candidates(conn: Connection, case_id: str, role_key: str, k: int) -> list[dict[str, Any]]:
    """Autoridades judiciales del expediente (línea de tiempo + entidades), normalizadas."""
    keywords = _AUTHORITY_KEYWORDS.get(role_key)
    if not keywords:
        return []
    pats = "{" + ",".join(f"%{kw}%" for kw in keywords) + "}"
    ev = rows(conn, """
        SELECT e.authority AS name, count(*) AS n,
               (array_agg(e.document_id ORDER BY e.event_date NULLS LAST))[1] AS document_id,
               (array_agg(e.page_number ORDER BY e.event_date NULLS LAST))[1] AS page_number,
               (array_agg(d.filename ORDER BY e.event_date NULLS LAST))[1] AS filename
        FROM events e LEFT JOIN documents d ON d.id = e.document_id
        WHERE e.case_id = :c AND e.kind = 'procedural' AND e.authority IS NOT NULL
          AND btrim(e.authority) <> '' AND e.authority ILIKE ANY(CAST(:pats AS text[]))
        GROUP BY e.authority""", c=case_id, pats=pats)
    en = rows(conn, """
        SELECT ent.name AS name, count(*) AS n, NULL::uuid AS document_id, NULL::int AS page_number,
               NULL::text AS filename
        FROM entities ent
        WHERE ent.case_id = :c AND ent.entity_type IN ('authority', 'organization')
          AND ent.name ILIKE ANY(CAST(:pats AS text[]))
        GROUP BY ent.name""", c=case_id, pats=pats)
    agg: dict[str, dict[str, Any]] = {}
    for r in list(ev) + list(en):
        key = _authority_signature(r["name"])
        if key.startswith("OTRO|") or key.endswith("|") or "||" in key:  # sin número ni clase → ruido
            continue
        g = agg.setdefault(key, {"display": r["name"], "best": 0, "n": 0,
                                 "document_id": None, "page_number": None, "filename": None})
        g["n"] += int(r["n"])
        if int(r["n"]) > g["best"]:
            g["best"], g["display"] = int(r["n"]), r["name"]
        if r["document_id"] and not g["document_id"]:
            g["document_id"], g["page_number"], g["filename"] = r["document_id"], r["page_number"], r["filename"]
    out: list[dict[str, Any]] = []
    for g in sorted(agg.values(), key=lambda g: g["n"], reverse=True)[:k]:
        # source_type document_page: las autoridades se citan por la página de sus
        # actuaciones (así cuentan como evidencia primaria citable, no como nota).
        out.append(evidence_item("AUT", "document_page", f"{g['display']} · {g['n']} actuaciones",
                                 document_id=g["document_id"], filename=g["filename"],
                                 page_number=g["page_number"] or 1,
                                 authority=g["display"], person_name=g["display"], role=role_key, mentions=g["n"]))
    return out


def _role_candidates_heuristic(conn: Connection, case_id: str, role_key: str,
                               keywords: tuple[str, ...], k: int = 15) -> list[dict[str, Any]]:
    """Candidatos por rol leídos de las firmas (heurístico): nombre, menciones y cita."""
    pats = "{" + ",".join(f"%{kw}%" for kw in keywords) + "}"
    pages = rows(conn, """
        SELECT p.document_id, d.filename, p.page_number, p.folio, p.text
        FROM document_pages p JOIN documents d ON d.id = p.document_id
        WHERE d.case_id = :c AND p.text ILIKE ANY(CAST(:pats AS text[]))
        ORDER BY d.filename, p.page_number
    """, c=case_id, pats=pats)
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    best: dict[str, tuple[str, str, str, int, str | None]] = {}
    for pg in pages:
        for name, snippet in _role_signature_matches(pg["text"] or "", keywords):
            key = re.sub(r"[^A-Z]", "", _strip_accents(name).upper())  # une variantes de OCR (espacios/puntos)
            if not key:
                continue
            counts[key] += 1
            display.setdefault(key, name)
            best.setdefault(key, (snippet, str(pg["document_id"]), pg["filename"], pg["page_number"], pg["folio"]))
    # Agrupa VARIANTES OCR del mismo nombre por solape de tokens y suma sus menciones.
    groups: list[dict[str, Any]] = []
    for key, n in counts.most_common():
        toks = _name_tokens(display[key])
        for g in groups:
            inter = toks & g["tokens"]
            union = toks | g["tokens"]
            same_tokens = bool(inter) and (len(inter) / len(union) >= 0.5
                                           or toks <= g["tokens"] or g["tokens"] <= toks)
            if toks and g["tokens"] and (same_tokens or _fuzzy_same(display[key], g["display"])):
                g["n"] += n
                if n > g["best_n"]:
                    g["best_n"], g["display"] = n, display[key]
                break
        else:
            snippet, did, fn, pn, folio = best[key]
            groups.append({"display": display[key], "tokens": toks, "n": n, "best_n": n,
                           "snippet": snippet, "did": did, "fn": fn, "pn": pn, "folio": folio})
    groups.sort(key=lambda g: g["n"], reverse=True)
    items: list[dict[str, Any]] = []
    for g in groups[:k]:
        text = f"{g['snippet']} ⟦{role_key}: {g['display']} · {g['n']} menciones⟧"
        items.append(evidence_item("PER", "document_page", text, document_id=g["did"], filename=g["fn"],
                                   page_number=g["pn"], folio=g["folio"], person_name=g["display"],
                                   role=role_key, mentions=g["n"]))
    return items


def _all_role_candidates(conn: Connection, case_id: str, k: int = 12) -> list[dict[str, Any]]:
    """Candidatos heurísticos de TODOS los roles, con tokens del nombre para emparejar."""
    entries: list[dict[str, Any]] = []
    for role_key, keywords in _ROLE_KEYWORDS.items():
        for it in _role_candidates_heuristic(conn, case_id, role_key, keywords, k):
            name = it.get("person_name") or ""
            entries.append({"role": role_key, "name": name, "tokens": _name_tokens(name),
                            "mentions": it.get("mentions", 0), "filename": it.get("filename"),
                            "page_number": it.get("page_number"),
                            "snippet": (it.get("text") or "").split("⟦")[0].strip()})
    return entries


_PARTY_SIDE_RE = re.compile(r"(?i)\b(demandante|demandado|denunciante|denunciado|ejecutante|ejecutado)\b")


def _party_side(snippet: str) -> str | None:
    m = _PARTY_SIDE_RE.search(snippet or "")
    return m.group(1).lower() if m else None


def _match_party(toks: set[str], side: str | None, parties: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Empareja la parte por nombre/alias (solape de tokens) o, si no, por el lado (rol de la parte)."""
    for p in parties:
        al = p.get("aliases") or []
        al_s = " ".join(al) if isinstance(al, (list, tuple)) else str(al)
        ptoks = _name_tokens(p["name"]) | _name_tokens(al_s)
        inter, union = toks & ptoks, toks | ptoks
        if toks and ptoks and inter and (len(inter) / len(union) >= 0.5 or toks <= ptoks or ptoks <= toks):
            return p
    if side:
        for p in parties:
            if side in (p.get("role") or "").lower():
                return p
    return None


def role_suggestions(conn: Connection, case_id: str) -> list[dict[str, Any]]:
    """Sugiere, por hablante: rol (heurística de firmas) y parte (por nombre o por el lado demandante/demandado)."""
    entries = _all_role_candidates(conn, case_id)
    parties = rows(conn, "SELECT id, name, role, aliases FROM parties WHERE case_id = :c ORDER BY name", c=case_id)
    sps = rows(conn, "SELECT id, label, display_name, speaker_role, resolved_party_id FROM speakers "
                     "WHERE case_id = :c ORDER BY label", c=case_id)
    out: list[dict[str, Any]] = []
    for sp in sps:
        toks = _name_tokens(sp["display_name"] or "")
        if not toks:
            continue
        best: dict[str, Any] | None = None
        for e in entries:
            inter = toks & e["tokens"]
            union = toks | e["tokens"]
            if toks and e["tokens"] and inter and (len(inter) / len(union) >= 0.5
                                                   or toks <= e["tokens"] or e["tokens"] <= toks):
                if best is None or e["mentions"] > best["mentions"]:
                    best = e
        side = _party_side(best["snippet"]) if best and best["role"] in ("apoderado", "parte") else None
        party = _match_party(toks, side, parties)
        if not best and not party:
            continue
        item: dict[str, Any] = {"speaker_id": str(sp["id"]), "label": sp["label"],
                                "display_name": sp["display_name"], "current_role": sp["speaker_role"],
                                "current_party_id": str(sp["resolved_party_id"]) if sp["resolved_party_id"] else None}
        if best:
            item.update(suggested_role=best["role"], mentions=best["mentions"],
                        filename=best["filename"], page_number=best["page_number"])
        if party:
            item.update(suggested_party_id=str(party["id"]), suggested_party_name=party["name"])
        if side:
            item["suggested_party_side"] = side
        out.append(item)
    return out




@register("list_low_confidence_pages",
          "Lista las páginas OCR con MENOR confianza del expediente (documento, página, motor y %). "
          "Úsala para '¿qué páginas tienen menor confianza?', '¿qué hojas revisar?' o '¿dónde está peor el OCR?'. "
          "La confianza arranca en 100% y baja al corregir contenido "
          "(= 100% − palabras editadas ÷ palabras totales).",
          {"limit": {"type": "integer", "default": 20, "description": "Máximo de páginas a devolver"},
           "max_confidence": {"type": "number", "default": 1.0, "description": "Umbral 0..1 (por defecto 1 = todas)"},
           "mode": {"type": "string", "enum": ["basico", "document_ai"],
                    "description": "Opcional: filtrar por motor OCR"}})
def list_low_confidence_pages(conn: Connection, case_id: str, ctx: ToolContext,
                              limit: int = 20, max_confidence: float = 1.0,
                              mode: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "c": case_id, "k": max(1, min(int(limit or 20), 200)),
        "mc": min(1.0, max(0.0, float(max_confidence if max_confidence is not None else 1.0))),
        "mode": mode if mode in ("basico", "document_ai") else None,
    }
    pages = rows(conn, """
        SELECT d.id AS document_id, d.filename, v.page_number, v.mode, v.ocr_confidence,
               p.folio, left(coalesce(v.text, p.text, ''), 220) AS snippet
        FROM document_ocr_versions v
        JOIN documents d ON d.id = v.document_id
        LEFT JOIN document_pages p ON p.document_id = v.document_id AND p.page_number = v.page_number
        WHERE d.case_id = :c AND v.ocr_confidence <= :mc
          AND (CAST(:mode AS text) IS NULL OR v.mode = :mode)
        ORDER BY v.ocr_confidence ASC, d.filename, v.page_number
        LIMIT :k""", **params)
    return [evidence_item("PG", "document_page",
                          f"{r['filename']} p.{r['page_number']} · {r['mode']} · "
                          f"confianza {round(float(r['ocr_confidence']) * 1000) / 10}% — {(r['snippet'] or '').strip()[:180]}",
                          document_id=str(r["document_id"]), filename=r["filename"], page_number=r["page_number"],
                          folio=r["folio"], mode=r["mode"], confidence=float(r["ocr_confidence"]),
                          role=None) for r in pages]


_REL_ES: dict[str, str] = {
    "causes": "causa", "responds_to": "responde a", "appeals": "apela a", "confirms": "confirma",
    "revokes": "revoca", "precedes": "precede", "refers_to": "refiere a", "same_as": "idéntico a",
}
_CAUSAL_RELS = ["causes", "responds_to", "appeals", "confirms", "revokes", "refers_to"]


def event_code_map(conn: Connection, case_id: str) -> dict[str, str]:
    """Código estable por actuación (EV-0001, EV-0002…) ordenando por fecha (procesales primero)."""
    evs = rows(conn, """SELECT id FROM events WHERE case_id = :c
                        ORDER BY (kind <> 'procedural'), event_date NULLS LAST, id""", c=case_id)
    return {str(e["id"]): f"EV-{i:04d}" for i, e in enumerate(evs, 1)}


def _resolve_event(conn: Connection, case_id: str, ref: str) -> dict[str, Any] | None:
    """Resuelve una actuación por id, código (EV-0004) o texto."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if re.fullmatch(r"[0-9a-fA-F-]{36}", ref):
        e = one(conn, "SELECT id, event_date, subtype, instance, actor, description FROM events WHERE id = :i AND case_id = :c",
                i=ref, c=case_id)
        if e:
            return e
    m = re.fullmatch(r"(?i)ev-?0*(\d+)", ref)
    if m:
        want = f"EV-{int(m.group(1)):04d}"
        for eid, code in event_code_map(conn, case_id).items():
            if code == want:
                return one(conn, "SELECT id, event_date, subtype, instance, actor, description FROM events WHERE id = :i AND case_id = :c",
                           i=eid, c=case_id)
    return one(conn, """SELECT id, event_date, subtype, instance, actor, description FROM events
                        WHERE case_id = :c AND kind = 'procedural'
                          AND (description ILIKE :q OR subtype ILIKE :q)
                        ORDER BY event_date NULLS LAST LIMIT 1""", c=case_id, q=f"%{ref}%")


@register("event_relations",
          "Relaciones de una actuación en el Process Graph: a qué actuaciones LLEVA y de qué depende "
          "(causes/responds_to/appeals/confirms/revokes/precedes/refers_to), con evidencia. "
          "Acepta id, código EV-0004 o texto (p. ej. 'sentencia de segunda instancia').",
          {"event": {"type": "string", "description": "id, EV-0004 o texto de la actuación"},
           "relationship": {"type": "string", "description": "Opcional: filtrar por tipo de relación"},
           "k": {"type": "integer", "default": 40}})
def event_relations(conn: Connection, case_id: str, ctx: ToolContext, event: str,
                    relationship: str | None = None, k: int = 40) -> list[dict[str, Any]]:
    ev = _resolve_event(conn, case_id, event)
    if not ev:
        return _err("No encontré la actuación (pasa id, EV-0004 o texto).")
    codes = event_code_map(conn, case_id)
    me = codes.get(str(ev["id"]), "?")
    rels = rows(conn, """
        SELECT r.relationship, r.confidence, r.source_event_id, r.target_event_id,
               se.event_date AS s_date, se.subtype AS s_subtype, se.description AS s_desc,
               te.event_date AS t_date, te.subtype AS t_subtype, te.description AS t_desc
        FROM event_relationships r
        JOIN events se ON se.id = r.source_event_id
        JOIN events te ON te.id = r.target_event_id
        WHERE r.case_id = :c AND (r.source_event_id = :e OR r.target_event_id = :e)
          AND (CAST(:rel AS text) IS NULL OR r.relationship = :rel)
        ORDER BY r.relationship LIMIT :k""",
        c=case_id, e=str(ev["id"]), rel=relationship, k=max(1, min(int(k or 40), 200)))
    out: list[dict[str, Any]] = []
    for r in rels:
        is_out = str(r["source_event_id"]) == str(ev["id"])
        other_id = str(r["target_event_id"]) if is_out else str(r["source_event_id"])
        other_code = codes.get(other_id, "?")
        desc = r["t_desc"] if is_out else r["s_desc"]
        subtype = r["t_subtype"] if is_out else r["s_subtype"]
        date = r["t_date"] if is_out else r["s_date"]
        arrow = "→" if is_out else "←"
        out.append(evidence_item("REL", "event_relation",
                                 f"{me} {arrow} {_REL_ES.get(r['relationship'], r['relationship'])} {other_code}: "
                                 f"{(desc or '')[:150]}",
                                 event_id=str(ev["id"]), event_code=me, relationship=r["relationship"],
                                 direction="out" if is_out else "in", other_id=other_id, other_code=other_code,
                                 other_subtype=subtype, other_date=str(date) if date else None,
                                 confidence=float(r["confidence"]) if r["confidence"] is not None else None))
    return out


@register("process_path",
          "Reconstruye la CADENA de una actuación por las relaciones del Process Graph "
          "(¿por qué se llegó a X?, ¿qué causó Y?): antecedentes (direction='to', por defecto) o "
          "consecuencias (direction='from'). Acepta id, código EV-0004 o texto; devuelve la secuencia "
          "cronológica con evidencia.",
          {"event": {"type": "string", "description": "id, EV-0004 o texto de la actuación"},
           "direction": {"type": "string", "enum": ["to", "from"], "default": "to",
                         "description": "to = antecedentes; from = consecuencias"},
           "depth": {"type": "integer", "default": 6},
           "include_precedes": {"type": "boolean", "default": False}})
def process_path(conn: Connection, case_id: str, ctx: ToolContext, event: str,
                 direction: str = "to", depth: int = 6, include_precedes: bool = False) -> list[dict[str, Any]]:
    target = _resolve_event(conn, case_id, event)
    if not target:
        return _err("No encontré la actuación (pasa id, EV-0004 o texto).")
    rels_allowed = list(_CAUSAL_RELS) + (["precedes"] if include_precedes else [])
    codes = event_code_map(conn, case_id)
    seen = {str(target["id"])}
    frontier = [str(target["id"])]
    for _ in range(max(1, min(int(depth or 6), 12))):
        nxt: list[str] = []
        for nid in frontier:
            if direction == "from":
                rs = rows(conn, """SELECT target_event_id AS nid FROM event_relationships
                                  WHERE case_id = :c AND source_event_id = :n AND relationship = ANY(:rels)""",
                          c=case_id, n=nid, rels=rels_allowed)
            else:
                rs = rows(conn, """SELECT source_event_id AS nid FROM event_relationships
                                  WHERE case_id = :c AND target_event_id = :n AND relationship = ANY(:rels)""",
                          c=case_id, n=nid, rels=rels_allowed)
            for r in rs:
                o = str(r["nid"])
                if o not in seen:
                    seen.add(o)
                    nxt.append(o)
        frontier = nxt
        if not frontier:
            break
    evs: list[dict[str, Any]] = []
    for eid in seen:
        e = one(conn, """SELECT id, event_date, subtype, instance, actor, description, document_id, page_number
                         FROM events WHERE id = :i AND case_id = :c""", i=eid, c=case_id)
        if e:
            evs.append(e)
    evs.sort(key=lambda e: (e["event_date"] is None, str(e["event_date"] or ""), str(e["id"])))
    out: list[dict[str, Any]] = []
    for e in evs:
        code = codes.get(str(e["id"]), "?")
        mark = " ★" if str(e["id"]) == str(target["id"]) else ""
        out.append(evidence_item("EV", "event",
                                 f"{code}{mark} · {e['event_date'] or 's/f'} | {e['subtype']} | {e['actor']} — "
                                 f"{(e['description'] or '')[:170]}",
                                 event_id=str(e["id"]), event_code=code,
                                 event_date=str(e["event_date"]) if e["event_date"] else None,
                                 subtype=e["subtype"], instance=e["instance"], actor=e["actor"],
                                 document_id=str(e["document_id"]) if e["document_id"] else None,
                                 page_number=e["page_number"]))
    return out


@register("search_transcripts",
          "Busca texto en las transcripciones de audio/video del expediente (con hablante y minuto).",
          {"query": {"type": "string"}, "k": {"type": "integer", "default": 5},
           "media_id": {"type": "string", "format": "uuid", "description": "Opcional: limitar a un video/audio"}})
def search_transcripts(conn: Connection, case_id: str, ctx: ToolContext, query: str, k: int = 5,
                       media_id: str | None = None) -> list[dict[str, Any]]:
    cfg = get_settings().FTS_CONFIG
    q = _tsquery(conn, query)
    if not q:
        return []
    params: dict[str, Any] = {"c": case_id, "cfg": cfg, "q": q, "k": k}
    media_filter = ""
    mid = _uuid(media_id) if media_id else None
    if media_id and not mid:
        return _err("media_id inválido")
    if mid:
        media_filter = "AND s.media_id = :mid"
        params["mid"] = mid
    rows_ = rows(conn, """
        SELECT s.id, s.media_id, s.start_ms, s.end_ms, sp.label AS speaker, sp.display_name AS speaker_name,
               left(s.text, 1500) AS text, m.filename
        FROM transcript_segments s
        JOIN media m ON m.id = s.media_id
        LEFT JOIN speakers sp ON sp.id = s.speaker_id
        WHERE m.case_id = :c AND s.tsv @@ to_tsquery(CAST(:cfg AS regconfig), :q) /*MEDIA_FILTER*/
        ORDER BY ts_rank_cd(s.tsv, to_tsquery(CAST(:cfg AS regconfig), :q)) DESC LIMIT :k
    """.replace("/*MEDIA_FILTER*/", media_filter), **params)
    return [_segment_item(r) for r in rows_]


@register("search_transcript_by_time",
          "Responde '¿en qué minuto se habló de X?' y '¿quién lo dijo?': busca en las transcripciones "
          "y devuelve cada segmento con su minuto exacto (mm:ss) y el hablante identificado. "
          "También lista lo dicho en un rango: from_minute/to_minute (ej. 'qué se dijo entre el min 10 y 15').",
          {"query": {"type": "string", "description": "Opcional: tema a buscar; vacío lista el rango"},
           "media_id": {"type": "string", "format": "uuid", "description": "Opcional: limitar a un video/audio"},
           "from_minute": {"type": "number", "description": "Opcional: minuto inicial (ej. 10 o 10.5)"},
           "to_minute": {"type": "number", "description": "Opcional: minuto final"},
           "k": {"type": "integer", "default": 10}})
def search_transcript_by_time(conn: Connection, case_id: str, ctx: ToolContext, query: str | None = None,
                              media_id: str | None = None, from_minute: float | None = None,
                              to_minute: float | None = None, k: int = 10) -> list[dict[str, Any]]:
    cfg = get_settings().FTS_CONFIG
    where, params = ["m.case_id = :c"], {"c": case_id, "cfg": cfg, "k": k}
    if media_id:
        mid = _uuid(media_id)
        if not mid:
            return _err("media_id inválido")
        where.append("s.media_id = :mid")
        params["mid"] = mid
    if from_minute is not None:
        where.append("s.end_ms >= :fms")
        params["fms"] = int(float(from_minute) * 60000)
    if to_minute is not None:
        where.append("s.start_ms <= :tms")
        params["tms"] = int(float(to_minute) * 60000)
    order = "s.start_ms"
    if query:
        q = _tsquery(conn, query)
        # El agente puede pasar la oración completa; aun así detectamos el nombre propio
        # («¿en qué minuto habló la doctora Paola?» → «Paola») para filtrar por HABLANTE.
        candidate = _speaker_candidate(query)
        params["like"] = f"%{candidate}%" if candidate else f"%{query.strip()}%"
        clauses = []
        if q:
            clauses.append("s.tsv @@ to_tsquery(CAST(:cfg AS regconfig), :q)")
            params["q"] = q
        clauses.append("(sp.display_name ILIKE :like OR sp.label ILIKE :like)")
        where.append("(" + " OR ".join(clauses) + ")")
        # Primero los segmentos DEL hablante buscado, luego las menciones; cronológico.
        order = "CASE WHEN sp.display_name ILIKE :like OR sp.label ILIKE :like THEN 0 ELSE 1 END, s.start_ms"
    rows_ = rows(conn, """
        SELECT s.id, s.media_id, s.start_ms, s.end_ms, sp.label AS speaker, sp.display_name AS speaker_name,
               left(s.text, 1500) AS text, m.filename
        FROM transcript_segments s
        JOIN media m ON m.id = s.media_id
        LEFT JOIN speakers sp ON sp.id = s.speaker_id
        WHERE /*WHERE_CLAUSE*/
        ORDER BY /*ORDER_CLAUSE*/ LIMIT :k
    """.replace("/*WHERE_CLAUSE*/", " AND ".join(where)).replace("/*ORDER_CLAUSE*/", order), **params)
    return [_segment_item(r, prefix="TR") for r in rows_]


@register("get_video_segment",
          "Obtiene el texto de un segmento de transcripción, por segment_id o por minuto (at_minute).",
          {"media_id": {"type": "string", "format": "uuid"},
           "segment_id": {"type": "string", "format": "uuid", "description": "Opcional si se usa at_minute"},
           "at_minute": {"type": "number", "description": "Opcional: minuto del video (ej. 14.5)"}})
def get_video_segment(conn: Connection, case_id: str, ctx: ToolContext, media_id: str,
                      segment_id: str | None = None, at_minute: float | None = None) -> list[dict[str, Any]]:
    mid = _uuid(media_id)
    if not mid:
        return _err("media_id inválido")
    if segment_id:
        sid = _uuid(segment_id)
        if not sid:
            return _err("segment_id inválido")
        r = rows(conn, """
            SELECT s.id, s.media_id, s.text, s.start_ms, s.end_ms, sp.label AS speaker,
                   sp.display_name AS speaker_name, m.filename
            FROM transcript_segments s JOIN media m ON m.id = s.media_id
            LEFT JOIN speakers sp ON sp.id = s.speaker_id
            WHERE s.id = :sid AND s.media_id = :mid AND m.case_id = :c
        """, sid=sid, mid=mid, c=case_id)
    elif at_minute is not None:
        ms = int(float(at_minute) * 60000)
        r = rows(conn, """
            SELECT s.id, s.media_id, s.text, s.start_ms, s.end_ms, sp.label AS speaker,
                   sp.display_name AS speaker_name, m.filename
            FROM transcript_segments s JOIN media m ON m.id = s.media_id
            LEFT JOIN speakers sp ON sp.id = s.speaker_id
            WHERE s.media_id = :mid AND m.case_id = :c AND s.start_ms <= :ms AND s.end_ms >= :ms
            ORDER BY s.start_ms LIMIT 1
        """, mid=mid, c=case_id, ms=ms)
        if not r:  # el minuto cae en un hueco: el segmento más cercano
            r = rows(conn, """
                SELECT s.id, s.media_id, s.text, s.start_ms, s.end_ms, sp.label AS speaker,
                       sp.display_name AS speaker_name, m.filename
                FROM transcript_segments s JOIN media m ON m.id = s.media_id
                LEFT JOIN speakers sp ON sp.id = s.speaker_id
                WHERE s.media_id = :mid AND m.case_id = :c
                ORDER BY abs(s.start_ms - :ms) LIMIT 1
            """, mid=mid, c=case_id, ms=ms)
    else:
        return _err("Se requiere segment_id o at_minute")
    if not r:
        return _err("Segmento no encontrado")
    return [_segment_item(r[0], prefix="S")]


def _page_items(conn: Connection, case_id: str, table: str, alias: str, clauses: list[str],
                params: dict, k: int, with_folio: bool, mode_expr: str, order: str = "file") -> list[dict[str, Any]]:
    """Evidencias de página desde una tabla de OCR (principal o versiones por motor)."""
    folio = f"{alias}.folio" if with_folio else "NULL"
    order_sql = (f"length({alias}.text) ASC, d.filename, {alias}.page_number" if order == "len"
                 else f"d.filename, {alias}.page_number")
    if not clauses:
        return []
    sql = """
        SELECT d.id AS document_id, d.filename, /*ALIAS*/.page_number, /*FOLIO*/ AS folio,
               left(/*ALIAS*/.text, 240) AS text, /*MODE*/ AS ocr_mode
        FROM /*TABLE*/ /*ALIAS*/ JOIN documents d ON d.id = /*ALIAS*/.document_id
        WHERE d.case_id = :c AND /*CLAUSES*/
        ORDER BY /*ORDER*/ LIMIT :k"""
    sql = (sql.replace("/*ALIAS*/", alias).replace("/*TABLE*/", table).replace("/*FOLIO*/", folio)
           .replace("/*MODE*/", mode_expr).replace("/*CLAUSES*/", " AND ".join(clauses))
           .replace("/*ORDER*/", order_sql))
    return [evidence_item("P", "document_page", r["text"], document_id=str(r["document_id"]),
                          filename=r["filename"], page_number=r["page_number"], folio=r.get("folio"),
                          ocr_mode=r.get("ocr_mode"))
            for r in rows(conn, sql, **params)]


def _dedupe_pages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out = []
    for it in items:
        if it.get("source_type") == "document_page":
            key = (str(it.get("document_id")), it.get("page_number"))
            if key in seen:
                continue
            seen.add(key)
        out.append(it)
    return out


@register("locate",
          "Lista TODAS las ubicaciones donde aparece un término: cada documento con su página y folio, "
          "y cada video/audio con su minuto y hablante. Úsala cuando el usuario pregunte dónde se habla/"
          "menciona algo o en qué archivos/páginas aparece.",
          {"term": {"type": "string", "description": "Término o frase a localizar (ej. 'pagaré 001')"},
           "k": {"type": "integer", "default": 300, "description": "Máximo de ubicaciones"}})
def locate(conn: Connection, case_id: str, ctx: ToolContext, term: str, k: int = 300) -> list[dict[str, Any]]:
    import unicodedata

    def fold(s: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))

    tokens = [t for t in re.split(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", term or "") if len(t) >= 2][:6]
    if not tokens:
        return _err("Se requiere un término de búsqueda")
    params: dict[str, Any] = {"c": case_id, "k": k}
    doc_where, doc_where_v, seg_where = [], [], []
    speaker_fold = "translate(lower(coalesce(sp.display_name, sp.label, '')), 'áéíóúüñ', 'aeiouun')"
    text_fold_s = _FOLD_SQL.format(col="s.text")
    for i, tok in enumerate(tokens):
        key = f"t{i}"
        if tok.isdigit():
            params[key] = tok.lstrip("0") or "0"
            cond = "~ ('\\m0*' || :{k} || '\\M')".format(k=key)
            doc_where.append(f"{_FOLD_SQL.format(col='p.text')} {cond}")
            doc_where_v.append(f"{_FOLD_SQL.format(col='v.text')} {cond}")
            seg_where.append(f"({text_fold_s} {cond} OR {speaker_fold} {cond})")
        else:
            params[key] = f"%{fold(tok)}%"
            doc_where.append(f"{_FOLD_SQL.format(col='p.text')} LIKE :{key}")
            doc_where_v.append(f"{_FOLD_SQL.format(col='v.text')} LIKE :{key}")
            seg_where.append(f"({text_fold_s} LIKE :{key} OR {speaker_fold} LIKE :{key})")
    out = _page_items(conn, case_id, "document_pages", "p", doc_where, params, k, True, "'pagina'", order="file")
    out += _page_items(conn, case_id, "document_ocr_versions", "v", doc_where_v, params, k, False, "v.mode", order="file")
    out = _dedupe_pages(out)
    segs = rows(conn, """
        SELECT s.id, s.media_id, s.start_ms, s.end_ms, sp.label AS speaker, sp.display_name AS speaker_name,
               left(s.text, 220) AS text, m.filename
        FROM transcript_segments s JOIN media m ON m.id = s.media_id
        LEFT JOIN speakers sp ON sp.id = s.speaker_id
        WHERE m.case_id = :c AND (/*SEG_CLAUSES*/)
        ORDER BY m.filename, s.start_ms LIMIT :k""".replace("/*SEG_CLAUSES*/", " OR ".join(seg_where)), **params)
    out += [_segment_item(r, prefix="TR") for r in segs]
    return out[:k] if k else out


# ---------------------------------------------------------------------------
# Archivos del expediente (para "tráeme el PDF" y el @ del chat)
# ---------------------------------------------------------------------------

def _file_item(kind: str, r: dict[str, Any], case_id: str) -> dict[str, Any]:
    fid = str(r["id"])
    base = f"/cases/{case_id}/{'documents' if kind == 'document' else 'media'}/{fid}"
    name = r.get("title") or r["filename"]
    extra = ""
    if kind == "document":
        extra = f", {r.get('page_count') or '?'} páginas"
    elif r.get("duration_ms"):
        extra = f", duración {mmss(r['duration_ms'])}"
    item = evidence_item("A", "file", f"Archivo: {name} ({'PDF' if kind == 'document' else 'video/audio'}{extra})",
                         kind=kind, filename=r["filename"], name=name,
                         size_bytes=r.get("size_bytes"), mime_type=r.get("mime_type"),
                         download_path=f"{base}/download", view_path=f"{base}/download",
                         sha256=(r.get("sha256") or "")[:12])
    if kind == "document":
        item["document_id"] = fid
        item["page_count"] = r.get("page_count")
        item["ocr_mode"] = r.get("ocr_mode")
        item["ocr_modes_available"] = r.get("ocr_modes") or []
    else:
        item["media_id"] = fid
        item["duration_ms"] = r.get("duration_ms")
    return item


@register("get_file",
          "'Tráeme el PDF/video': localiza un archivo del expediente por nombre (o id) y devuelve "
          "una tarjeta con sus datos y la URL para VERLO y DESCARGARLO.",
          {"query": {"type": "string", "description": "Nombre (o parte) del archivo, ej. '001' o 'CuadernoPrincipal'"},
           "document_id": {"type": "string", "format": "uuid"},
           "media_id": {"type": "string", "format": "uuid"}})
def get_file(conn: Connection, case_id: str, ctx: ToolContext, query: str | None = None,
             document_id: str | None = None, media_id: str | None = None) -> list[dict[str, Any]]:
    if document_id or media_id:
        fid = _uuid(document_id or media_id)
        if not fid:
            return _err("id inválido")
        if document_id:
            r = rows(conn, """SELECT d.id, d.filename, NULL AS title, d.size_bytes, d.mime_type, d.sha256,
                       d.page_count, d.ocr_mode,
                       (SELECT array_agg(DISTINCT v.mode) FROM document_ocr_versions v WHERE v.document_id = d.id) AS ocr_modes
                       FROM documents d WHERE d.id = :i AND d.case_id = :c""", i=fid, c=case_id)
            return [_file_item("document", r[0], case_id)] if r else _err("Documento no encontrado en este expediente")
        r = rows(conn, "SELECT id, filename, title, size_bytes, mime_type, sha256, duration_ms FROM media WHERE id = :i AND case_id = :c",
                 i=fid, c=case_id)
        return [_file_item("media", r[0], case_id)] if r else _err("Video/audio no encontrado en este expediente")
    if not query:
        return _err("Se requiere query, document_id o media_id")
    like = f"%{query}%"
    docs = rows(conn, """SELECT d.id, d.filename, NULL AS title, d.size_bytes, d.mime_type, d.sha256,
                   d.page_count, d.ocr_mode,
                   (SELECT array_agg(DISTINCT v.mode) FROM document_ocr_versions v WHERE v.document_id = d.id) AS ocr_modes
                   FROM documents d WHERE d.case_id = :c AND d.filename ILIKE :q ORDER BY d.filename LIMIT 5""",
                c=case_id, q=like)
    vids = rows(conn, """SELECT id, filename, title, size_bytes, mime_type, sha256, duration_ms
                   FROM media WHERE case_id = :c AND (filename ILIKE :q OR title ILIKE :q) ORDER BY filename LIMIT 5""",
                c=case_id, q=like)
    if not docs and not vids:
        return _err(f"Ningún archivo del expediente coincide con '{query}'")
    return [_file_item("document", d, case_id) for d in docs] + [_file_item("media", v, case_id) for v in vids]


@register("list_case_files",
          "Lista los archivos del expediente (PDFs y videos/audios) con su id, nombre y tipo. "
          "Útil para resolver referencias como '@001' o 'el cuaderno principal'.",
          {"kind": {"type": "string", "enum": ["document", "media"], "description": "Opcional: solo PDFs o solo videos"},
           "query": {"type": "string", "description": "Opcional: filtrar por nombre"},
           "limit": {"type": "integer", "default": 50}})
def list_case_files(conn: Connection, case_id: str, ctx: ToolContext, kind: str | None = None,
                    query: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    like = f"%{query}%" if query else None
    out: list[dict[str, Any]] = []
    if kind in (None, "document"):
        sql = """SELECT d.id, d.filename, NULL AS title, d.size_bytes, d.mime_type, d.sha256, d.page_count, d.ocr_mode,
                   (SELECT array_agg(DISTINCT v.mode) FROM document_ocr_versions v WHERE v.document_id = d.id) AS ocr_modes
                   FROM documents d WHERE d.case_id = :c"""
        params: dict[str, Any] = {"c": case_id}
        if like:
            sql += " AND d.filename ILIKE :q"
            params["q"] = like
        sql += " ORDER BY d.filename LIMIT :lim"
        params["lim"] = limit
        out += [_file_item("document", d, case_id) for d in rows(conn, sql, **params)]
    if kind in (None, "media"):
        sql = "SELECT id, filename, title, size_bytes, mime_type, sha256, duration_ms FROM media WHERE case_id = :c"
        params = {"c": case_id}
        if like:
            sql += " AND (filename ILIKE :q OR title ILIKE :q)"
            params["q"] = like
        sql += " ORDER BY filename LIMIT :lim"
        params["lim"] = limit
        out += [_file_item("media", v, case_id) for v in rows(conn, sql, **params)]
    return out


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

@register("graph_query",
          "Navega el knowledge graph desde un nodo (personas, claims, hechos, eventos, decisiones).",
          {"start_node_id": {"type": "string", "format": "uuid"},
           "depth": {"type": "integer", "default": 2},
           "edge_type": {"type": "string", "description": "Opcional: ASSERTS, SUPPORTS, REFUTES, CITES, ABOUT, ..."}})
def graph_query(conn: Connection, case_id: str, ctx: ToolContext, start_node_id: str, depth: int = 2,
                edge_type: str | None = None) -> list[dict[str, Any]]:
    nid = _uuid(start_node_id)
    if not nid:
        return _err("start_node_id inválido")
    result = graph.traverse(conn, case_id, nid, depth=min(int(depth), 3),
                            edge_types=[edge_type] if edge_type else None)
    return [evidence_item("G", "graph_node", f"{n['node_type']}: {n['label']}",
                          node_id=str(n["id"]), node_type=n["node_type"],
                          source_table=n.get("source_table"), source_id=str(n.get("source_id")))
            for n in result.get("nodes", [])[:20]]


@register("graph_neighbors",
          "Vecinos directos de un nodo del grafo con el tipo de relación (A -[SUPPORTS]-> B).",
          {"node_id": {"type": "string", "format": "uuid"},
           "edge_type": {"type": "string", "description": "Opcional: filtrar por tipo de arista"},
           "limit": {"type": "integer", "default": 20}})
def graph_neighbors(conn: Connection, case_id: str, ctx: ToolContext, node_id: str,
                    edge_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    nid = _uuid(node_id)
    if not nid:
        return _err("node_id inválido")
    params: dict[str, Any] = {"c": case_id, "n": nid, "lim": limit}
    type_filter = ""
    if edge_type:
        type_filter = "AND e.edge_type = :et"
        params["et"] = edge_type
    rels = rows(conn, """
        SELECT e.edge_type, e.confidence, e.provenance,
               s.id AS source_id, s.node_type AS source_type_, s.label AS source_label,
               t.id AS target_id, t.node_type AS target_type_, t.label AS target_label
        FROM graph_edges e
        JOIN graph_nodes s ON s.id = e.source_node_id
        JOIN graph_nodes t ON t.id = e.target_node_id
        WHERE e.case_id = :c AND (e.source_node_id = :n OR e.target_node_id = :n) /*TYPE_FILTER*/
        LIMIT :lim
    """.replace("/*TYPE_FILTER*/", type_filter), **params)
    out = []
    for r in rels:
        text = f"{r['source_label']} -[{r['edge_type']}]-> {r['target_label']}"
        other_id = r["target_id"] if str(r["source_id"]) == nid else r["source_id"]
        out.append(evidence_item("G", "graph_edge", text, node_id=str(other_id),
                                 edge_type=r["edge_type"], confidence=r["confidence"],
                                 provenance=r["provenance"]))
    return out


@register("find_person",
          "'¿Quién es X?': busca personas en el grafo del caso y trae sus afirmaciones (claims), "
          "participaciones en eventos y testimonios relacionados.",
          {"name": {"type": "string", "description": "Nombre o parte del nombre de la persona"},
           "k": {"type": "integer", "default": 5}})
def find_person(conn: Connection, case_id: str, ctx: ToolContext, name: str, k: int = 5) -> list[dict[str, Any]]:
    if not name or not name.strip():
        return _err("Se requiere un nombre para buscar personas")
    term = name.strip()
    people = graph.find_node(conn, case_id, node_type="Person", query=term, limit=k)
    out: list[dict[str, Any]] = []
    for person in people:
        out.append(evidence_item("G", "graph_node", f"Persona: {person['label']}",
                                 node_id=str(person["id"]), node_type="Person",
                                 source_table=person.get("source_table"),
                                 source_id=str(person.get("source_id")),
                                 metadata=person.get("metadata") or {}))
        rels = rows(conn, """
            SELECT e.edge_type, n.node_type, n.label, n.id AS node_id, n.source_table, n.source_id
            FROM graph_edges e JOIN graph_nodes n ON n.id = e.target_node_id
            WHERE e.case_id = :c AND e.source_node_id = :p
            UNION ALL
            SELECT e.edge_type, n.node_type, n.label, n.id AS node_id, n.source_table, n.source_id
            FROM graph_edges e JOIN graph_nodes n ON n.id = e.source_node_id
            WHERE e.case_id = :c AND e.target_node_id = :p
            LIMIT 15
        """, c=case_id, p=str(person["id"]))
        for r in rels:
            out.append(evidence_item("G", "graph_edge", f"{person['label']} -[{r['edge_type']}]-> {r['node_type']}: {r['label']}",
                                     node_id=str(r["node_id"]), edge_type=r["edge_type"],
                                     related_node_type=r["node_type"], related_label=r["label"],
                                     source_table=r.get("source_table"), source_id=str(r.get("source_id"))))
    # Fuentes CITABLES donde aparece el nombre (documentos y transcripciones), aunque
    # no exista una ficha de persona en el grafo. Así "¿quién es X?" nunca se queda sin respuesta.
    mentions: list[dict[str, Any]] = []
    try:
        mentions = answering.retrieve(conn, case_id, term)[: max(k, 5)]
    except Exception:  # noqa: BLE001
        mentions = []
    if not out and mentions:
        out.append(evidence_item("G", "graph_note",
                                 f"No hay una ficha de persona en el grafo para '{term}', "
                                 "pero estas fuentes del expediente lo mencionan."))
    out.extend(mentions)
    if not out:
        return _err(f"No se encontró '{term}' ni en el grafo ni en documentos ni en transcripciones del expediente")
    return out


# ---------------------------------------------------------------------------
# Claims, hechos, evidencia y línea de tiempo
# ---------------------------------------------------------------------------

@register("search_claims",
          "Busca claims/alegaciones extraídas de documentos o testimonios.",
          {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}})
def search_claims(conn: Connection, case_id: str, ctx: ToolContext, query: str, k: int = 5) -> list[dict[str, Any]]:
    where, params = _ilike_terms(query)
    if not where:
        return []
    base = """
        SELECT cl.id, cl.text, cl.claim_type, cl.confidence, pa.name AS claimant,
               d.filename, cl.created_at
        FROM claims cl
        LEFT JOIN parties pa ON pa.id = cl.claimant_party_id
        LEFT JOIN documents d ON d.id = (SELECT source_document_id FROM evidence WHERE id = cl.id LIMIT 1)
        WHERE cl.case_id = :c AND (/*WHERE_CLAUSE*/)
        ORDER BY cl.created_at DESC LIMIT :k
    """
    rows_ = rows(conn, base.replace("/*WHERE_CLAUSE*/", where), c=case_id, k=k, **params)
    return [evidence_item("C", "claim", r["text"], claim_id=str(r["id"]), claim_type=r["claim_type"],
                          claimant=r["claimant"], filename=r["filename"]) for r in rows_]


@register("search_facts",
          "Busca hechos (facts) ya extraídos y verificados.",
          {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}})
def search_facts(conn: Connection, case_id: str, ctx: ToolContext, query: str, k: int = 5) -> list[dict[str, Any]]:
    where, params = _ilike_terms(query, column="proposition")
    if not where:
        return []
    base = """
        SELECT id, proposition, status, confidence FROM facts
        WHERE case_id = :c AND (/*WHERE_CLAUSE*/)
        LIMIT :k
    """
    rows_ = rows(conn, base.replace("/*WHERE_CLAUSE*/", where), c=case_id, k=k, **params)
    return [evidence_item("F", "fact", r["proposition"], fact_id=str(r["id"]), status=r["status"],
                          confidence=r["confidence"]) for r in rows_]


@register("search_evidence",
          "Busca ítems de evidencia documental/testimonial.",
          {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}})
def search_evidence(conn: Connection, case_id: str, ctx: ToolContext, query: str, k: int = 5) -> list[dict[str, Any]]:
    where, params = _ilike_terms(query, column="description")
    if not where:
        return []
    base = """
        SELECT e.id, e.description, e.evidence_type, d.filename
        FROM evidence e
        LEFT JOIN documents d ON d.id = e.source_document_id
        WHERE e.case_id = :c AND (/*WHERE_CLAUSE*/)
        LIMIT :k
    """
    rows_ = rows(conn, base.replace("/*WHERE_CLAUSE*/", where), c=case_id, k=k, **params)
    return [evidence_item("EV", "evidence", r["description"], evidence_id=str(r["id"]),
                          evidence_type=r["evidence_type"], filename=r["filename"]) for r in rows_]


def _timeline(conn: Connection, case_id: str, query: str | None, start_date: str | None,
              end_date: str | None, k: int) -> list[dict[str, Any]]:
    where, params = _ilike_terms(query or "", column="description")
    base = "SELECT id, event_type, event_date, description, timeline_confidence FROM events WHERE case_id = :c"
    params["c"] = case_id
    if where:
        base += " AND (/*WHERE_CLAUSE*/)"
    if start_date:
        base += " AND event_date >= :s"
        params["s"] = start_date
    if end_date:
        base += " AND event_date <= :e"
        params["e"] = end_date
    base += " ORDER BY event_date NULLS LAST LIMIT :k"
    params["k"] = k
    rows_ = rows(conn, base.replace("/*WHERE_CLAUSE*/", where) if where else base, **params)
    return [evidence_item("T", "event", r["description"], event_id=str(r["id"]), event_type=r["event_type"],
                          event_date=str(r["event_date"]), timeline_confidence=r["timeline_confidence"]) for r in rows_]


@register("search_timeline",
          "Busca eventos en la línea de tiempo del caso.",
          {"query": {"type": "string"},
           "start_date": {"type": "string", "description": "YYYY-MM-DD opcional"},
           "end_date": {"type": "string", "description": "YYYY-MM-DD opcional"}})
def search_timeline(conn: Connection, case_id: str, ctx: ToolContext, query: str,
                    start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    return _timeline(conn, case_id, query, start_date, end_date, 10)


@register("get_timeline",
          "Línea de tiempo del proceso: eventos ordenados por fecha (opcionalmente filtrados por tema o rango).",
          {"query": {"type": "string", "description": "Opcional: tema a filtrar"},
           "start_date": {"type": "string", "description": "YYYY-MM-DD opcional"},
           "end_date": {"type": "string", "description": "YYYY-MM-DD opcional"},
           "k": {"type": "integer", "default": 15}})
def get_timeline(conn: Connection, case_id: str, ctx: ToolContext, query: str | None = None,
                 start_date: str | None = None, end_date: str | None = None, k: int = 15) -> list[dict[str, Any]]:
    return _timeline(conn, case_id, query, start_date, end_date, k)
