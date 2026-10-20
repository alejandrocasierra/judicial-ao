"""Vocabulario y comparación de términos compartidos por router, retrieval,
compositor, abstención y ocurrencias.

Sin dependencias: solo Python estándar. Todo el pipeline usa estas funciones
para que la definición de "término de contenido" sea UNA sola en todo el sistema.
"""
from __future__ import annotations

import re
import unicodedata

# Palabras interrogativas, conectores y verbos de petición: no identifican el
# tema de la pregunta.
QUESTION_WORDS: frozenset[str] = frozenset({
    "que", "qué", "quien", "quién", "quienes", "quiénes", "cual", "cuál", "cuales", "cuáles",
    "cuanto", "cuánto", "cuanta", "cuánta", "cuantos", "cuántos", "cuantas", "cuántas",
    "como", "cómo", "cuando", "cuándo", "donde", "dónde", "porque", "por qué", "para", "por",
    "cuyo", "cuya", "el", "la", "los", "las", "un", "una", "unos", "unas",
    "del", "de", "al", "a", "en", "con", "sin", "sobre", "entre", "desde", "hasta", "hacia",
    "es", "son", "fue", "fueron", "ser", "esta", "está", "estan", "están", "este", "esto",
    "estos", "estas", "ese", "esa", "esos", "esas", "hay", "tiene", "tienen", "hace", "hacen",
    "me", "te", "se", "le", "les", "lo", "mi", "tu", "su", "sus", "nos",
    "puedes", "podrias", "podrías", "decir", "dime", "cuentame", "cuéntame", "resume", "resumen",
    "explica", "explicar", "dame", "muestra", "muestrame", "muéstrame", "sabes", "saber",
    "conocer", "informacion", "información", "trata", "consta", "obra", "ver", "listar", "lista",
    "encontrar", "busca", "buscar", "encuentra", "localiza", "ubicar", "aparece", "aparecen",
    "menciona", "mencionan", "menciono", "mencionó", "habla", "hablan", "hablo", "habló",
    "dice", "dicen", "dijo", "dijeron", "señala", "senalar", "señalar", "indica", "indican",
    "registra", "registran", "figura", "figuran", "existe", "existen", "contiene",
    "presenta", "presentan", "solicita", "solicitan", "realiza", "realizan", "tramita",
})

# Palabras de "reporte": describen la búsqueda, no el tema buscado. Si se usan
# como término de locate, producen ruido masivo ("evidencia pagos").
REPORT_WORDS: frozenset[str] = frozenset({
    "evidencia", "evidencias", "prueba", "pruebas", "probatorio", "probatoria",
    "documento", "documentos", "archivo", "archivos", "expediente", "expedientes",
    "carpeta", "caso", "casos", "hecho", "hechos", "dato", "datos", "soporte", "soportes",
    "informacion", "información", "detalle", "detalles", "resumen", "resúmenes",
    "pagina", "página", "paginas", "páginas", "folio", "folios", "anexo", "anexos",
    "video", "vídeo", "videos", "vídeos", "audio", "audios", "grabacion", "grabación",
    "grabaciones", "audiencia", "audiencias", "transcripcion", "transcripción",
    "minuto", "minutos", "segundo", "segundos", "hora", "horas", "momento", "momentos",
    "parte", "partes", "seccion", "sección", "apartado", "capitulo", "capítulo",
    "doctor", "doctora", "señor", "señora", "senor", "senora", "dicho", "dicha",
})

STOPWORDS: frozenset[str] = frozenset({
    "a", "al", "algo", "algun", "algún", "alguna", "alguno", "algunos", "ante", "antes",
    "aquel", "aquella", "aquello", "aun", "aún", "bajo", "cabe", "cada", "casi", "como",
    "cómo", "con", "contra", "cual", "cuál", "cuales", "cuáles", "cuando", "cuándo",
    "cuanta", "cuánta", "cuanto", "cuánto", "cuantos", "cuántos", "de", "debe", "del",
    "desde", "despues", "después", "donde", "dónde", "durante", "e", "el", "ella", "ello",
    "ellos", "en", "entre", "era", "eran", "es", "esa", "ese", "eso", "esos", "esta",
    "está", "estan", "están", "estar", "este", "esto", "estos", "fue", "fueron", "ha",
    "haber", "habia", "había", "han", "has", "hasta", "hay", "haya", "he", "hubo", "la",
    "las", "le", "les", "lo", "los", "mas", "más", "me", "mi", "mí", "mia", "mía", "mio",
    "mío", "muy", "nada", "ni", "no", "nos", "nosotros", "o", "os", "otra", "otro",
    "otros", "para", "pero", "podeis", "podéis", "poder", "podria", "podría", "por",
    "porque", "pudo", "pueda", "puede", "pueden", "pues", "que", "qué", "quien", "quién",
    "quienes", "quiénes", "se", "sea", "sean", "segun", "según", "ser", "sera", "será",
    "si", "sí", "sin", "sobre", "son", "su", "sus", "tambien", "también", "tan", "tanto",
    "te", "tenia", "tenía", "tener", "ti", "tiene", "tienen", "todo", "todos", "tras",
    "tu", "tú", "tus", "un", "una", "uno", "unos", "vosotros", "vuestra", "vuestro", "y",
    "ya", "yo",
})

# Sinónimos jurídicos conservadores. Solo se usan para AMPLIAR la consulta
# (nunca para afirmar hechos), y siempre con el término original primero.
LEGAL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "pago": ("abono", "consignacion", "consignación", "transferencia", "comprobante"),
    "pagos": ("abonos", "consignaciones", "transferencias", "comprobantes"),
    "abono": ("pago", "consignacion", "consignación"),
    "transferencia": ("pago", "consignacion", "consignación", "comprobante"),
    "consignacion": ("pago", "abono", "transferencia"),
    "consignación": ("pago", "abono", "transferencia"),
    "comprobante": ("pago", "transferencia", "recibo"),
    "pagaré": ("pagare", "título valor", "titulo valor"),
    "pagare": ("pagaré", "título valor", "titulo valor"),
    "demanda": ("escrito de demanda", "libelo"),
    "demandas": ("escritos de demanda", "libelos"),
    "contestacion": ("contestación", "respuesta a la demanda"),
    "contestación": ("contestacion", "respuesta a la demanda"),
    "apelacion": ("apelación", "recurso de apelación", "alzada"),
    "apelación": ("apelacion", "recurso de apelación", "alzada"),
    "apelaciones": ("recursos de apelación", "alzadas"),
    "sentencia": ("fallo", "providencia", "decisión"),
    "sentencias": ("fallos", "providencias", "decisiones"),
    "contrato": ("acuerdo", "convenio"),
    "contratos": ("acuerdos", "convenios"),
    "arrendamiento": ("arriendo", "locación", "locacion"),
    "trabajador": ("empleado", "servidor"),
    "trabajadores": ("empleados", "servidores"),
    "despido": ("terminación del contrato", "terminacion del contrato"),
    "indemnizacion": ("indemnización", "resarcimiento"),
    "indemnización": ("indemnizacion", "resarcimiento"),
    "honorarios": ("remuneración", "remuneracion", "pago de servicios"),
    "perito": ("experticia", "dictamen pericial"),
    "peritos": ("expertos", "dictámenes periciales"),
    "testigo": ("declaración", "declaracion", "testimonio"),
    "testigos": ("declaraciones", "testimonios"),
    "juez": ("juzgado", "despacho judicial", "autoridad judicial"),
    "jueces": ("juzgados", "despachos judiciales", "autoridades judiciales"),
}

_TOKEN = re.compile(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:[.,][0-9]+)*", re.U)
_NUMBER = re.compile(r"\d[\d.,]*", re.U)


def norm(text: str | None) -> str:
    """Minúsculas y sin diacríticos. Determinista para cualquier idioma latino."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def raw_tokens(text: str | None, min_len: int = 2) -> list[str]:
    out: list[str] = []
    for tok in _TOKEN.findall(text or ""):
        bare = tok.rstrip(".,")
        if len(bare) >= min_len:
            out.append(bare)
    return out


def tokens(text: str | None, min_len: int = 3) -> list[str]:
    """Tokens normalizados, sin stopwords ni palabras de reporte."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in raw_tokens(text, min_len=min_len):
        key = norm(tok)
        if len(key) < min_len or key in seen:
            continue
        if key in STOPWORDS or key in QUESTION_WORDS or key in REPORT_WORDS:
            continue
        seen.add(key)
        out.append(key)
    return out


def content_terms(text: str | None, min_len: int = 3) -> list[str]:
    """Términos que identifican el tema de una pregunta o afirmación."""
    return tokens(text, min_len=min_len)


def stem(word: str) -> str:
    """Stemmer español mínimo y determinista (plurales y terminaciones).

    No sustituye a un lematizador: solo evita que "pagos" y "pago" se traten
    como temas distintos en el grounding y el rerank.
    """
    w = norm(word)
    if len(w) <= 4:
        return w
    if w.endswith("ces") and len(w) > 5:
        return w[:-3] + "z"          # jueces -> juez
    if w.endswith("es") and len(w) > 5:
        return w[:-2] if w[-3] in "lrndz" else w[:-1]
    if w.endswith("s") and len(w) > 4:
        return w[:-1]                 # pagos -> pago, demandas -> demanda
    return w


def content_set(text: str | None, min_len: int = 3, *, use_stem: bool = True) -> set[str]:
    terms = content_terms(text, min_len=min_len)
    return {stem(t) for t in terms} if use_stem else set(terms)


def coverage(claim: str | None, evidence: str | None) -> float:
    """Proporción de términos del claim presentes en la evidencia (0..1)."""
    terms = content_set(claim)
    if not terms:
        return 1.0
    ev = content_set(evidence)
    return sum(1 for t in terms if t in ev) / len(terms)


def overlap_with_question(claim: str | None, question: str | None) -> float:
    """Proporción de términos de la PREGUNTA presentes en el claim (0..1).

    Se usa para decidir si un claim responde a la pregunta o solo la roza.
    """
    q = content_set(question)
    if not q:
        return 1.0
    c = content_set(claim)
    return sum(1 for t in q if t in c) / len(q)


def numbers(text: str | None) -> list[str]:
    return [re.sub(r"[.,]", "", n) for n in _NUMBER.findall(text or "")]


def missing_numbers(claim: str | None, evidence: str | None) -> list[str]:
    ev = set(numbers(evidence))
    return [n for n in numbers(claim) if n not in ev]


def mmss(ms: object) -> str:
    try:
        total = int(ms) // 1000  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def specific_term(question: str | None) -> str | None:
    """El término más específico para un `locate` (no la pregunta entera).

    Orden de preferencia:
    1. frase entre comillas;
    2. identificador numérico (oficio 2834, NIT 521.606);
    3. sigla/nombre en mayúsculas (INVERFAST, PAGARE);
    4. término de contenido, penalizando palabras genéricas ("acumulada").
    """
    text = question or ""
    quoted = re.search(r"[\"“«']([^\"”»']{2,80})[\"”»']", text)
    if quoted:
        return stem(quoted.group(1).strip())

    terms = content_terms(text)
    if not terms:
        return None

    generic = {
        "oficio", "numero", "num", "radicado", "proceso", "procesal", "ejecutiva", "ejecutivo",
        "acumulada", "acumulado", "cautelar", "cautelares", "medidas", "demanda", "contestacion",
        "sentencia", "auto", "providencia", "recurso", "apelacion", "audiencia", "diligencia",
    }

    def _score(item: tuple[int, str]) -> tuple[float, int]:
        idx, term = item
        score = 0.0
        if any(ch.isdigit() for ch in term):
            score += 4.0 + min(len(term), 10) * 0.2
        if term.isupper() and len(term) > 2:
            score += 2.0
        if norm(term) in generic:
            score -= 4.0
        # El objeto de la pregunta suele ir al final ("... sobre pagos").
        score += idx * 0.1
        return score, idx

    chosen_idx, chosen = max(enumerate(terms), key=_score)
    if any(ch.isdigit() for ch in chosen):
        # "pagaré 001" / "NIT 521.606": el identificador solo no basta; se
        # acompaña del sustantivo anterior no genérico para acotar el locate.
        for prev in reversed(terms[:chosen_idx]):
            if not any(ch.isdigit() for ch in prev) and norm(prev) not in generic:
                return f"{stem(prev)} {chosen}"
        return chosen
    return stem(chosen)


def expand_query(question: str) -> list[str]:
    """Variantes deterministas de la consulta (original + sinónimos jurídicos).

    No inventa hechos: solo amplía el vocabulario de búsqueda. La primera
    variante es siempre la pregunta original.
    """
    variants = [question]
    for term in content_terms(question):
        for syn in LEGAL_SYNONYMS.get(term, ()):
            variants.append(syn)
    # Deduplicar preservando orden.
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = norm(v).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(v.strip())
    return out
