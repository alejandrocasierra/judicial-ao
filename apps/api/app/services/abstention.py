"""Detector de abstención: evita que RAG/agente invente cuando la pregunta
contiene términos ausentes del expediente (SSD §33, §98).

No es un modelo de NLI; es una capa de seguridad léxica barata y determinista
antes de gastar tokens en un LLM. Comprueba la cobertura de términos sobre
todo texto indexado del caso: documentos, transcripciones, claims, hechos,
evidencia, eventos, partes, speakers y nombres de archivo.
"""
from __future__ import annotations

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
    """Extrae términos significativos de la pregunta (sin stopwords).

    Devuelve RAÍCES: "pagos" -> "pago", para que el ILIKE encuentre también
    "pago" y no se abstenga por una diferencia morfológica.
    """
    from app.services import text_terms

    # Los números no se usan para abstención: el mismo monto puede aparecer como
    # "$80.000.000" o "80000000". El grounding del claim sí los exige después.
    return [text_terms.stem(t) for t in text_terms.content_terms(question)
            if not t[0].isdigit()]


def term_coverage(conn: Connection, case_id: str, question: str) -> float:
    terms = _extract_terms(question)
    if not terms:
        return 0.0
    present = rows(conn, """
        SELECT term FROM unnest(CAST(:terms AS text[])) AS term
        WHERE EXISTS (
            SELECT 1 FROM chunks
            WHERE case_id = :c AND search_tsv @@ plainto_tsquery('spanish', term)
        )
        OR EXISTS (
            SELECT 1 FROM document_pages p
            JOIN documents d ON d.id = p.document_id
            WHERE d.case_id = :c AND p.search_tsv @@ plainto_tsquery('spanish', term)
        )
        OR EXISTS (
            SELECT 1 FROM transcript_segments s
            JOIN media m ON m.id = s.media_id
            WHERE m.case_id = :c AND s.search_tsv @@ plainto_tsquery('spanish', term)
        )
        OR EXISTS (
            SELECT 1 FROM documents
            WHERE case_id = :c AND filename ILIKE '%' || term || '%'
        )
        OR EXISTS (
            SELECT 1 FROM parties
            WHERE case_id = :c AND name ILIKE '%' || term || '%'
        )
        OR EXISTS (
            SELECT 1 FROM speakers
            WHERE case_id = :c AND (label ILIKE '%' || term || '%' OR display_name ILIKE '%' || term || '%')
        )
    """, terms=terms, c=case_id)
    return len(present) / len(terms)


def should_abstain(conn: Connection, case_id: str, question: str, min_coverage: float | None = None) -> bool:
    """Devuelve True si la pregunta no está suficientemente cubierta por el expediente."""
    if min_coverage is None:
        min_coverage = get_settings().ABSTENTION_MIN_TERM_COVERAGE
    return term_coverage(conn, case_id, question) < min_coverage
