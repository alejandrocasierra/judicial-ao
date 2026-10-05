"""Recordatorio general para el agente del chat.

Antes aquí había reglas por palabra clave («si dice video → transcripción», «si dice
pagaré → documento»). Eso era frágil y demasiado específico. Ahora el recordatorio
es GENERAL: la respuesta puede estar en documentos (OCR), audiencias (ASR) o el grafo,
y el agente debe buscar en las fuentes necesarias sin que se le adivinen palabras.

El único matiz de ACCIÓN (no de búsqueda) es la corrección de OCR/ASR, que vive en
`app.services.correction`.
"""
from __future__ import annotations

import re

_GENERAL = (
    "Recuerda: la respuesta puede estar en CUALQUIERA de las fuentes del expediente — "
    "documentos (texto OCR, con página y folio), audiencias/audios/videos (transcripción ASR, "
    "con minuto exacto y hablante) o el grafo (personas, hechos y relaciones). No supongas de "
    "antemano dónde está: empieza con una búsqueda amplia (search_case) y luego afina con las "
    "herramientas específicas (documentos: read_document/get_document_page; audiencias: "
    "search_transcript_by_time/get_video_segment; personas: find_person/graph_neighbors). "
    "Si una herramienta no devuelve nada útil, prueba OTRA antes de rendirte: busca el término "
    "tanto en documentos como en transcripciones. No respondas «no hay evidencia» sin haber "
    "probado ambos. Ten en cuenta variantes de escritura: el OCR suele venir en MAYÚSCULAS y "
    "sin tildes. Cita siempre la fuente exacta: archivo + página (o archivo + minuto + hablante). "
    "Si el usuario pregunta DÓNDE se habla o menciona algo, o EN QUÉ ARCHIVO/PÁGINA aparece, usa "
    "la herramienta locate y enumera cada ubicación (archivo + página/minuto), sin omitir ninguna."
)


def hint(text: str | None = None) -> str:
    """Recordatorio general (independiente de las palabras de la pregunta)."""
    if text and _ROLE_QUESTION.search(text):
        return _GENERAL + " " + _ROLE_HINT
    return _GENERAL


# --- Agregación por rol: «¿cuántos jueces han intervenido?», «¿quiénes son los apoderados?» ---
_ROLE_QUESTION = re.compile(
    r"cu[áa]nt[oa]s?\s+(juec|juez|magistrad|apoderad|abogad|testig|perit|fiscal|secretari)|"
    r"qui[ée]nes\s+(han\s+intervenido|intervinieron|participaron|actuaron|son\s+los|son\s+las)|"
    r"(jueces|magistrados|apoderados|abogados|testigos|peritos|fiscales|secretarios)"
    r"[^.]{0,40}(interven|actuar|particip)",
    re.IGNORECASE,
)
_ROLE_HINT = (
    "Esta es una pregunta de ROL/AGREGACIÓN: usa list_people_by_role con el rol pedido (juez, apoderado, "
    "testigo, perito, secretario, fiscal, parte). Devuelve los nombres CANDIDATOS con su número de menciones "
    "y su cita; cuenta los candidatos distintos y cita cada uno. Es heurístico (puede traer ruido): dilo y "
    "verifica. NO la respondas con un solo fragmento de transcripción."
)


def merge(*hints: str | None) -> list[str] | None:
    """Une varias pistas descartando las vacías."""
    out = [h for h in hints if h]
    return out or None


# --- Localización: «¿dónde se habla de X?», «¿en qué archivos/páginas aparece X?» ---
_LOCATE = re.compile(
    r"(d[óo]nde|en\s+qu[ée]\s+(archivo|documento|p[áa]gina|parte|cuaderno)|"
    r"se\s+(habla|habl[óo]|menciona|mencion[óo]|trata|dice)|qui[ée]n\s+(menciona|habla)|"
    r"aparece|ubicaci[óo]n|en\s+qu[ée]\s+otros?\s+archivos?)",
    re.IGNORECASE,
)
_LOCATE_DROP = {
    "donde", "dónde", "en", "que", "qué", "cual", "cuál", "archivo", "archivos", "documento", "documentos",
    "pagina", "página", "paginas", "páginas", "parte", "minuto", "minutos", "video", "videos", "cuaderno",
    "se", "habla", "hablo", "habló", "menciona", "menciono", "mencionó", "trata", "dice", "dijo", "quien",
    "quién", "quienes", "quiénes", "aparece", "ubicacion", "ubicación", "el", "la", "los", "las", "del",
    "de", "al", "un", "una", "otro", "otros", "otra", "otras", "mas", "más", "sobre", "por", "para", "y",
    "o", "es", "son", "esta", "está", "estan", "están", "tal", "tales", "listar", "lista", "muestra",
}


def locate_term(text: str | None) -> str | None:
    """Si el usuario pide DÓNDE aparece algo, devuelve el término a localizar; si no, None."""
    t = text or ""
    if not _LOCATE.search(t):
        return None
    quoted = re.search(r"[\"'«]([^\"'»]{2,})[\"'»]", t)
    if quoted:
        return quoted.group(1).strip()
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{2,}", t)
    keep = [w for w in words if w.lower() not in _LOCATE_DROP]
    term = " ".join(keep[-4:]).strip()
    return term or None
