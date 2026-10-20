"""Expansión de consultas por grafo de entidades (contrato §1.3, fase 3).

Lee `graph_nodes.label` del caso buscando los términos de contenido de la
pregunta y añade esas etiquetas (y sus alias declarados en
`metadata->>'aliases'`) como variantes de búsqueda. Es una expansión de
vocabulario: no afirma hechos ni añade citas.

Todo es defensivo: si el grafo no existe, está vacío o la consulta falla, se
devuelven las consultas originales sin interrumpir el turno.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.services import text_terms

log = logging.getLogger(__name__)

MAX_TERMS = 5
MAX_QUERIES = 8
MAX_LABEL_CHARS = 120


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            continue
        key = text_terms.norm(clean)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def _aliases(value: Any) -> list[str]:
    """Alias desde `metadata.aliases`: lista JSON, array de Postgres o texto separado."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int))]
    if not isinstance(value, str):
        return []
    raw = value.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if isinstance(v, (str, int))]
        except Exception:  # noqa: BLE001 — metadata sucia no rompe la búsqueda
            return []
    return [part for part in raw.replace(";", ",").split(",") if part.strip()]


def _rows(conn: Connection, sql: str, **params: Any) -> list[dict]:
    result = conn.execute(text(sql), params)
    # SQLAlchemy real: .mappings() es un MÉTODO. Los dobles de prueba pueden
    # exponer una lista o un objeto con .all(); se aceptan ambas formas.
    mappings = getattr(result, "mappings", None)
    if callable(mappings):
        return [dict(row) for row in mappings().all()]
    if mappings is not None:
        rows = mappings.all() if hasattr(mappings, "all") else mappings
        return [dict(row) for row in rows]
    return []


def expand_queries(conn: Connection, case_id: str, question: str,
                   queries: Sequence[str]) -> list[str]:
    """Añade etiquetas del grafo a las consultas. Nunca lanza."""
    original = _dedupe(queries)
    terms = text_terms.content_terms(question)[:MAX_TERMS]
    if not terms:
        return original[:MAX_QUERIES]

    try:
        params: dict[str, Any] = {"c": case_id}
        clauses: list[str] = []
        for i, term in enumerate(terms):
            params[f"t{i}"] = f"%{term}%"
            clauses.append(f"label ILIKE :t{i}")
        sql = (
            "SELECT label, metadata FROM graph_nodes "
            "WHERE case_id = :c AND (" + " OR ".join(clauses) + ") "
            "ORDER BY label LIMIT 40"
        )
        found = _rows(conn, sql, **params)
    except Exception as exc:  # noqa: BLE001 — el grafo es opcional
        log.warning("graph expansion failed: %s", exc)
        return original[:MAX_QUERIES]

    additions: list[str] = []
    for row in found:
        label = row.get("label")
        if isinstance(label, str) and 2 < len(label) <= MAX_LABEL_CHARS:
            additions.append(label)
        meta = row.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:  # noqa: BLE001
                meta = {}
        if isinstance(meta, dict):
            additions.extend(_aliases(meta.get("aliases")))

    out = _dedupe(original + additions)
    return out[:MAX_QUERIES]


__all__ = ["expand_queries"]
