"""Diccionario de términos por organización (mejora continua del OCR/ASR).

Cada vez que un humano corrige una página OCR o un segmento de transcripción, los
términos significativos del texto corregido se acumulan en `ocr_terms`. Ese lexicón
puede usarse luego para post-corregir OCR (symspellpy/rapidfuzz) y así subir la
confianza en documentos futuros.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import text
from sqlalchemy.engine import Connection

_TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ\-]{3,}")


def normalize(term: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", term.lower()) if not unicodedata.combining(ch))


def learn_terms(conn: Connection, org_id: str, content: str) -> int:
    """Registra (o incrementa) los términos significativos del texto corregido."""
    seen: set[str] = set()
    terms: list[tuple[str, str]] = []
    for raw in _TOKEN_RE.findall(content or ""):
        norm = normalize(raw)
        if len(norm) < 4 or norm in seen:
            continue
        seen.add(norm)
        terms.append((raw.lower(), norm))
    for raw, norm in terms:
        conn.execute(
            text("""INSERT INTO ocr_terms (organization_id, term, normalized, occurrences)
                    VALUES (:o, :t, :n, 1)
                    ON CONFLICT (organization_id, normalized)
                    DO UPDATE SET occurrences = ocr_terms.occurrences + 1, term = :t, updated_at = now()"""),
            {"o": org_id, "t": raw, "n": norm},
        )
    return len(terms)
