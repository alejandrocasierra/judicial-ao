"""Contexto verificable derivado de los metadatos de la fuente (F4 · R1).

El dato que el modelo cita a menudo no vive en el texto de la página o del
segmento, sino en su etiqueta: el nombre del archivo («13Dic2024»), el hablante
resuelto («JORGE H. ROJAS M.»), el minuto o el folio. Sin este módulo, el gate
legal comparaba la fecha del claim contra el cuerpo del texto y bloqueaba
afirmaciones correctas.

Funciones puras: no consultan la base de datos ni la red. Todo lo que devuelven
deriva del nombre del archivo y de los campos del ítem; nunca se inventa un dato
que la fuente no traiga.
"""
from __future__ import annotations

import re
from typing import Any

from app.services import text_terms

# --- vocabulario de fechas ---------------------------------------------------------

_MONTH_NUMBERS: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_MONTH_CANON = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
)
_MONTH_ALT = "|".join(sorted(_MONTH_NUMBERS, key=len, reverse=True))
_MONTH_BY_PREFIX = {name[:3]: number for name, number in _MONTH_NUMBERS.items()}

_DMY_TEXT = re.compile(r"\b(\d{1,2})\s*(?:de\s+)?(" + _MONTH_ALT + r")\s*(?:de\s+)?(\d{2,4})\b")
# El OCR de los pagarés suele escribir el mes primero: «Bogotá ABRIL 4 de 2008».
_MDY_TEXT = re.compile(r"\b(" + _MONTH_ALT + r")\s+(\d{1,2})\s*(?:de\s+|del\s+)?(\d{2,4})\b")
_DMY_NUM = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})(?!\d)")
_YMD_NUM = re.compile(r"(?<!\d)(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})(?!\d)")
_COMPACT_DMY = re.compile(r"(?<!\d)(\d{1,2})([a-z]{3,12})(\d{4})(?!\d)")

# Número de radicado: 2018-361, 2018.361 y 201800361.
_SEQ_DASHED = re.compile(r"(?<!\d)(\d{4})[-.](\d{1,6})(?!\d)(?![-.]\d)")
_SEQ_JOINED = re.compile(r"(?<!\d)(\d{4})(\d{3,6})(?!\d)")

# --- utilidades de texto -----------------------------------------------------------

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_WORD = re.compile(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")


def _norm(value: str | None) -> str:
    return text_terms.norm(value)


def _dedupe(parts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        clean = " ".join(str(part or "").split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _month_number(word: str) -> int | None:
    folded = _norm(word)
    if folded in _MONTH_NUMBERS:
        return _MONTH_NUMBERS[folded]
    return _MONTH_BY_PREFIX.get(folded[:3])


def _date_candidates(text: str) -> list[tuple[int, int, int]]:
    """(día, mes, año) de todas las fechas reconocibles del texto."""
    folded = _norm(text)
    out: list[tuple[int, int, int]] = []
    for match in _DMY_TEXT.finditer(folded):
        month = _month_number(match.group(2))
        if month:
            out.append((int(match.group(1)), month, int(match.group(3))))
    for match in _MDY_TEXT.finditer(folded):
        month = _month_number(match.group(1))
        if month:
            out.append((int(match.group(2)), month, int(match.group(3))))
    for match in _COMPACT_DMY.finditer(folded):
        month = _month_number(match.group(2))
        if month:
            out.append((int(match.group(1)), month, int(match.group(3))))
    for match in _YMD_NUM.finditer(folded):
        out.append((int(match.group(3)), int(match.group(2)), int(match.group(1))))
    for match in _DMY_NUM.finditer(folded):
        out.append((int(match.group(1)), int(match.group(2)), int(match.group(3))))
    return out


def _same_date(left: tuple[int, int, int], right: tuple[int, int, int]) -> bool:
    if left[0] != right[0] or left[1] != right[1]:
        return False
    if left[2] >= 100 and right[2] >= 100:
        return left[2] == right[2]
    return left[2] % 100 == right[2] % 100


def _date_variants(stem: str) -> list[str]:
    """Formas de fecha legibles para el nombre de archivo."""
    out: list[str] = []
    for day, month, year in _date_candidates(stem):
        name = _MONTH_CANON[month - 1]
        out.append(f"{day} {name} {year}")
        out.append(f"{day} de {name} de {year}")
        out.append(f"{day}/{month:02d}/{year}")
        out.append(f"{year}-{month:02d}-{day:02d}")
        out.append(f"{day}{name[:3].capitalize()}{year}")
    return out


def _case_number_variants(stem: str) -> list[str]:
    out: list[str] = []
    for pattern in (_SEQ_DASHED, _SEQ_JOINED):
        for match in pattern.finditer(stem):
            year, sequence = match.group(1), match.group(2)
            bare = sequence.lstrip("0") or sequence
            out.extend([year, sequence, bare, f"{year}-{bare}"])
    return out


def filename_context(filename: str | None) -> str:
    """Contexto derivado del nombre del archivo (fechas, radicado, palabras)."""
    raw = str(filename or "").strip()
    if not raw:
        return ""
    stem = _EXTENSION.sub("", raw)
    spaced = _CAMEL_BOUNDARY.sub(" ", stem)
    spaced = " ".join(re.sub(r"[_\-./]+", " ", spaced).split())
    parts = [raw, stem, spaced]
    parts.extend(_date_variants(stem))
    parts.extend(_case_number_variants(stem))
    return " ".join(_dedupe(parts))


_CONTEXT_LABEL_FIELDS = ("speaker", "start_mmss", "page_number", "folio", "name", "label")
_CONTEXT_FACT_FIELDS = ("fact_label", "fact_text", "proof_status", "fact_number", "claim_text")


def context(item: dict[str, Any]) -> str:
    """Texto citable + metadatos visibles de un ítem de evidencia.

    Conserva todos los campos que ya usaban `answering._evidence_context` y
    `compose._context` (hechos sembrados, hablante, minuto, página, folio) y
    añade el contexto del nombre del archivo. No añade nada que no venga del
    propio ítem.
    """
    if not isinstance(item, dict):
        return ""
    parts: list[str] = []
    text = item.get("text")
    if text not in (None, ""):
        parts.append(str(text))
    filename = item.get("filename")
    if filename not in (None, ""):
        parts.append(str(filename))
        derived = filename_context(str(filename))
        if derived:
            parts.append(derived)
    for key in _CONTEXT_LABEL_FIELDS + _CONTEXT_FACT_FIELDS:
        value = item.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    return " ".join(_dedupe(parts))


def date_supported(date_value: str, context: str) -> bool:
    """True solo si día, mes y año de la fecha aparecen en el contexto.

    Se comparan las formas `d de mes de yyyy`, `d/m/yyyy`, `d-m-yyyy`,
    `yyyy-mm-dd` y `dmesyyyy`, tolerando tildes, mayúsculas y separadores. El
    año suelto no basta: una fecha sin su día y su mes no está soportada.
    """
    value = str(date_value or "").strip()
    if not value:
        return False
    haystack = str(context or "")
    candidates = _date_candidates(value)
    if not candidates:
        # Formato no reconocido: solo vale la coincidencia literal.
        return bool(haystack) and _norm(value) in _norm(haystack)
    if not haystack:
        return False
    if _norm(value) in _norm(haystack):
        return True
    return any(_same_date(candidates[0], found) for found in _date_candidates(haystack))


# --- nombres ----------------------------------------------------------------------

_CORPORATE_TAIL = (
    ("s", "a", "s"), ("s", "a"), ("sas",), ("sa",), ("ltda",), ("limitada",),
    ("inc",), ("corp",), ("corporation",),
)
_PUNCTUATION = re.compile(r"[.,;:()\[\]\"'«»¿?¡!]")


def _name_tokens(value: str | None) -> list[str]:
    folded = _PUNCTUATION.sub(" ", _norm(value))
    tokens = folded.split()
    while tokens:
        for tail in _CORPORATE_TAIL:
            if tuple(tokens[-len(tail):]) == tail:
                del tokens[-len(tail):]
                break
        else:
            break
    return tokens


def _token_compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) == 1:
        return right.startswith(left)
    if len(right) == 1:
        return left.startswith(right)
    return False


def _first_compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    return len(left) >= 4 and len(right) >= 4 and left[:4] == right[:4]


def _token_present(token: str, tokens: list[str]) -> bool:
    return any(_token_compatible(token, other) for other in tokens)


def _shared_tokens(left: list[str], right: list[str]) -> int:
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return sum(1 for token in shorter if _token_present(token, longer))


def name_compatible(left: str, right: str) -> bool:
    """Dos formas del mismo nombre: completa vs. iniciales o solo apellidos.

    - igualdad tras normalizar y quitar sufijos societarios;
    - una forma contenida en la otra (alias abreviado: «ROJAS MELO»);
    - mismo apellido (o su inicial) y al menos dos tokens compartidos. Si las
      dos formas traen nombres de pila completos, el primero debe coincidir:
      «Juan Carlos Garzón Gutiérrez» no es «Carlos Alfonso Garzón Gutiérrez».
    """
    l_tokens = _name_tokens(left)
    r_tokens = _name_tokens(right)
    if not l_tokens or not r_tokens:
        return False
    if l_tokens == r_tokens:
        return True
    if len(l_tokens) == 1 or len(r_tokens) == 1:
        return False
    shorter, longer = ((l_tokens, r_tokens) if len(l_tokens) <= len(r_tokens)
                       else (r_tokens, l_tokens))
    if all(_token_present(token, longer) for token in shorter):
        return True
    if not _token_compatible(l_tokens[-1], r_tokens[-1]):
        return False
    if _shared_tokens(l_tokens, r_tokens) < 2:
        return False
    if len(l_tokens) >= 3 and len(r_tokens) >= 3 and not _first_compatible(l_tokens[0], r_tokens[0]):
        return False
    return True


def _mentions(text: str, name: str) -> bool:
    """El texto nombra a esa parte (nombre completo, alias o forma compatible)."""
    if not text or not name:
        return False
    folded = _norm(text)
    if _norm(name) and _norm(name) in folded:
        return True
    tokens = _name_tokens(name)
    if not tokens:
        return False
    words = _WORD.findall(folded)
    if not words:
        return False
    anchors = [token for token in tokens if len(token) >= 3]
    starts = [
        index for index, word in enumerate(words)
        if any(word == token or (len(word) >= 3 and word[:3] == token[:3])
               for token in anchors)
    ]
    if not starts:
        return False
    size = len(tokens)
    lengths = sorted({max(1, size - 2), max(1, size - 1), size, size + 1})
    # Nombres de pila que, inmediatamente antes de la ventana, indican que el
    # match empieza en un token interno: «Juan Carlos Garzón» no es «Carlos
    # Garzón». Sin este guardarraíl, los hermanos Garzón se confundían.
    blockers = {
        "juan", "jose", "luis", "miguel", "pedro", "ana", "maria", "carlos",
        "jorge", "diego", "andres", "francisco", "alberto", "fernando", "sergio",
        "raul", "oscar", "ivan", "hector", "manuel", "alejandro", "jaime",
    }
    # Una mención puede empezar en el primer nombre o en el primer apellido:
    # «a favor de Rojas Melo» y «el demandado Garzón Gutiérrez» son válidas.
    surname = tokens[-2] if len(tokens) >= 3 else tokens[-1]
    allowed_starts = {tokens[0], surname}
    for start in starts:
        if start > 0 and words[start] not in allowed_starts:
            continue
        if start > 0 and words[start] == surname and len(tokens) >= 3:
            given = tokens[:-2]
            window = words[max(0, start - len(given)):start]
            if any(word in blockers for word in window) and window[-len(given):] != given:
                continue
        if start > 0 and words[start - 1] in blockers and words[start - 1] not in tokens:
            continue
        for length in lengths:
            candidate = " ".join(words[start:start + length])
            if candidate and name_compatible(name, candidate):
                return True
    return False


def party_present(claim: str, context: str, names: list[str]) -> bool:
    """True si el contexto respalda la mención de parte que hace el claim.

    Si el claim no nombra a la parte, no hay nada que respaldar y devuelve True.
    Si la nombra y el contexto (texto, hablante resuelto o alias) no la
    contiene, devuelve False: el hallazgo conservador se mantiene.
    """
    claim_text = str(claim or "")
    cited = str(context or "")
    for name in names or []:
        value = str(name or "").strip()
        if not value or not _mentions(claim_text, value):
            continue
        if not _mentions(cited, value):
            return False
    return True
