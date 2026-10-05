"""Post-proceso del texto OCR: limpia artefactos de formato y deja una salida organizada.

Los formularios judiciales escaneados traen tres tipos de ruido que ensucian el texto
extraído y que no aportan información:

1. **Reglas de formulario**: líneas de puntos o guiones bajos que dibujan los campos en
   blanco (``original:____________``). El OCR las fusiona con las etiquetas y produce
   textos como ``Folios Correspondientes en original:_``.
2. **Glifos basura**: el reconocedor multilingüe (PP-OCR) ocasionalmente emite
   ideogramas CJK, símbolos de uso privado o caracteres de reemplazo en zonas de sello.
3. **Huecos de columna**: varias marcas de tabulación/espacio que no aportan estructura.

Este módulo no altera el orden de lectura (ya reconstruido por ``_layout_text``) ni la
alineación por columnas; sólo elimina el ruido y normaliza los espacios en blanco.
"""
from __future__ import annotations

import re
import unicodedata

# Línea compuesta únicamente por caracteres de "regla" (campos en blanco de un formulario).
_RULE_LINE_RE = re.compile(r"^[\s_\-–—=~.·•*+]{3,}$")
# Secuencias de guiones bajos (campos vacíos) que el OCR a veces une a texto.
_UNDERSCORE_RUN_RE = re.compile(r"\s*_{2,}\s*")
# Espacios/tabulaciones repetidos al final de línea.
_TRAILING_SPACES_RE = re.compile(r"[ \t]+$")
# Líneas en blanco consecutivas (máximo una).
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _is_garbage_char(ch: str) -> bool:
    """True para caracteres que nunca pertenecen a un expediente redactado en español."""
    if ch in "\n\r\t":
        return False
    if ch == "\ufffd":  # carácter de reemplazo
        return True
    cat = unicodedata.category(ch)
    if cat in {"Co", "Cn", "Cs", "Cf"}:  # uso privado, no asignado, sustituto, formato
        return True
    cp = ord(ch)
    # Ideogramas y signos CJK (incluye radicales y símbolos de ancho completo).
    return 0x2E80 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF or 0xFF00 <= cp <= 0xFFEF


def strip_garbage_chars(text: str) -> str:
    """Elimina glifos basura (CJK, uso privado, reemplazo) del texto OCR."""
    return "".join(ch for ch in text if not _is_garbage_char(ch))


def clean_form_artifacts(text: str) -> str:
    """Quita reglas de formulario y guiones bajos sueltos conservando el resto.

    Las líneas que no traen guiones bajos se dejan intactas (conservan la alineación
    por columnas reconstruida por el OCR). Las que sí los traen se normalizan: cada
    guion bajo pasa a ser un separador (p. ej. la casilla ``_X_`` queda como ``X``).
    """
    out: list[str] = []
    for raw in text.splitlines():
        if _RULE_LINE_RE.match(raw):
            continue
        if "_" not in raw:
            out.append(raw.rstrip())
            continue
        line = _UNDERSCORE_RUN_RE.sub(" ", raw).replace("_", " ")
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        out.append(line)
    return "\n".join(out)


def normalize_ocr_text(text: str) -> str:
    """Pipeline de limpieza aplicado al texto OCR antes de guardarlo.

    Orden: espacios duros -> glifos basura -> artefactos de formulario -> espacios y
    líneas en blanco. Devuelve el texto sin espacios sobrantes al inicio/final.
    """
    if not text:
        return text or ""
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = strip_garbage_chars(text)
    text = clean_form_artifacts(text)
    text = "\n".join(_TRAILING_SPACES_RE.sub("", line) for line in text.splitlines())
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
