"""Detector de abstención: evita que RAG/agente invente cuando la pregunta
contiene términos ausentes del expediente (SSD §33, §98).

No es un modelo de NLI; es una capa de seguridad léxica barata y determinista
antes de gastar tokens en un LLM. Comprueba la cobertura de términos sobre
todo texto indexado del caso: documentos, transcripciones, claims, hechos,
evidencia, eventos, partes, speakers y nombres de archivo.
"""
from __future__ import annotations

import re

from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import rows

_STOPWORDS = {
    "a", "al", "algo", "algún", "alguna", "alguno", "algunos", "ante", "antes", "aquel", "aquella", "aquello",
    "aun", "aún", "bajo", "cabe", "cada", "casi", "como", "cómo", "con", "contra", "cual", "cuál", "cuales",
    "cuáles", "cuando", "cuándo", "cuanta", "cuánta", "cuanto", "cuánto", "cuantos", "cuántos", "de", "debe",
    "del", "desde", "después", "donde", "dónde", "durante", "e", "el", "ella", "ello", "ellos", "en", "entre",
    "era", "eran", "es", "esa", "ese", "eso", "esos", "esta", "está", "están", "estar", "este", "esto", "estos",
    "fue", "fueron", "ha", "haber", "había", "han", "has", "hasta", "hay", "haya", "he", "hubo", "la", "las",
    "le", "les", "lo", "los", "mas", "más", "me", "mi", "mí", "mía", "mío", "muy", "nada", "ni", "no", "nos",
    "nosotros", "o", "os", "otra", "otro", "otros", "para", "pero", "podeis", "podéis", "poder", "podria",
    "podría", "por", "porque", "pudo", "pueda", "puede", "pueden", "pues", "que", "qué", "quien", "quién",
    "quienes", "quiénes", "se", "sea", "sean", "según", "ser", "será", "si", "sí", "sin", "sobre", "son", "su",
    "sus", "también", "tan", "tanto", "te", "tenía", "tener", "ti", "tiene", "tienen", "todo", "todos", "tras",
    "tu", "tú", "tus", "un", "una", "uno", "unos", "vosotros", "vuestra", "vuestro", "y", "ya", "yo",
}


def _extract_terms(question: str) -> list[str]:
    """Extrae términos significativos de la pregunta (sin stopwords)."""
    tokens = re.findall(r"[a-záéíóúüñ0-9]+(?:[.,][0-9]+)*", question.lower())
    seen: set[str] = set()
    out = []
    for t in tokens:
        if len(t) >= 3 and t not in _STOPWORDS and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def term_coverage(conn: Connection, case_id: str, question: str) -> float:
    terms = _extract_terms(question)
    if not terms:
        return 0.0
    present = rows(conn, """
        SELECT term FROM unnest(CAST(:terms AS text[])) AS term
        WHERE EXISTS (
            SELECT 1 FROM (
                SELECT text FROM chunks WHERE case_id = :c
                UNION ALL
                SELECT p.text FROM document_pages p
                JOIN documents d ON d.id = p.document_id
                WHERE d.case_id = :c
                UNION ALL
                SELECT s.text FROM transcript_segments s
                JOIN media m ON m.id = s.media_id
                WHERE m.case_id = :c
                UNION ALL
                SELECT text FROM claims WHERE case_id = :c
                UNION ALL
                SELECT proposition FROM facts WHERE case_id = :c
                UNION ALL
                SELECT description FROM evidence WHERE case_id = :c
                UNION ALL
                SELECT description FROM events WHERE case_id = :c
                UNION ALL
                SELECT name FROM parties WHERE case_id = :c
                UNION ALL
                SELECT label FROM speakers WHERE case_id = :c
                UNION ALL
                SELECT filename FROM documents WHERE case_id = :c
            ) src
            WHERE src.text ILIKE '%' || term || '%'
            LIMIT 1
        )
    """, terms=terms, c=case_id)
    return len(present) / len(terms)


def should_abstain(conn: Connection, case_id: str, question: str, min_coverage: float | None = None) -> bool:
    """Devuelve True si la pregunta no está suficientemente cubierta por el expediente."""
    if min_coverage is None:
        min_coverage = get_settings().ABSTENTION_MIN_TERM_COVERAGE
    return term_coverage(conn, case_id, question) < min_coverage
