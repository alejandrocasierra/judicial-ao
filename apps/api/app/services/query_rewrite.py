"""Reescritura de consulta para el embudo de recuperación (contrato §1.3).

Determinista por defecto: primera variante = pregunta original, luego una
variante con el contexto del turno anterior cuando la pregunta es elíptica
(«¿y eso?», «lo mismo», «y los pagos»), y por último los sinónimos jurídicos de
`text_terms.expand_query`.

El LLM es opcional y solo se usa si `QUERY_REWRITE_ENABLED` está activo y
`LLM_ROUTING` define la tarea `query_rewrite`. Sus variantes se validan: sin
números inventados, sin texto vacío y con longitud acotada. Cualquier fallo cae
a la reescritura determinista.

Nunca inventa hechos: solo produce variantes de BÚSQUEDA.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services import text_terms

log = logging.getLogger(__name__)

MAX_VARIANTS = 8
MAX_VARIANT_CHARS = 280
_MAX_LLM_VARIANTS = 3

# Arranques elípticos que necesitan el turno anterior para tener sentido.
_ELLIPTICAL = (
    "y ", "e ", "eso", "esto", "esa ", "ese ", "aquel", "aquello",
    "lo mismo", "lo anterior", "tambien", "ademas", "entonces",
    "y sobre", "y del", "y de la",
)
# Deícticos que pueden aparecer en medio de una pregunta corta: «qué dijo X
# sobre lo anterior». Antes solo se miraba el arranque y estos seguimientos se
# quedaban sin contexto.
_DEICTIC_ANY = (
    "lo anterior", "sobre eso", "de eso", "sobre esto", "de esto",
    "lo mismo", "dicho", "al respecto", "esa misma", "ese mismo",
    "el mismo tema", "en ese punto",
)

# Pronombres personales: se comprueban sobre el texto original porque `norm`
# elimina la tilde y «él» quedaría indistinguible del artículo «el».
_PRONOUN_START = re.compile(r"^(el|ella|ellos|ellas)\b", re.I)


def _fold(text: str) -> str:
    return text_terms.norm(text).lstrip("¿¡ ").strip()


def _is_elliptical(question: str) -> bool:
    folded = _fold(question)
    if _PRONOUN_START.match((question or "").lower().lstrip("¿¡ ")):
        return True
    if any(folded.startswith(prefix) for prefix in _ELLIPTICAL):
        return True
    # Pregunta corta que contiene un deíctico: «qué dice Jorge Rojas sobre lo anterior».
    return len(folded.split()) <= 14 and any(marker in folded for marker in _DEICTIC_ANY)


def _history_text(turn: Any) -> str:
    if isinstance(turn, str):
        return turn
    if not isinstance(turn, dict):
        return ""
    for key in ("content", "text", "question", "message"):
        value = turn.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _history_role(turn: Any) -> str:
    if not isinstance(turn, dict):
        return "user"
    role = turn.get("role") or turn.get("sender") or turn.get("author") or "user"
    return str(role).lower()


def last_user_question(history: list[dict] | None) -> str | None:
    """Última pregunta del usuario en el historial (o None si no hay)."""
    for turn in reversed(history or []):
        if _history_role(turn) not in ("user", "usuario", "human"):
            continue
        text = _history_text(turn)
        if text:
            return text
    return None


def _numbers(text: str) -> set[str]:
    return set(text_terms.numbers(text))


def _valid_variant(variant: Any, source_numbers: set[str]) -> str | None:
    if not isinstance(variant, str):
        return None
    clean = " ".join(variant.split())
    if len(clean) < 3 or len(clean) > MAX_VARIANT_CHARS:
        return None
    # No se permiten cifras/fechas que no estuvieran ya en la conversación.
    if _numbers(clean) - source_numbers:
        return None
    return clean


def _dedupe(variants: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for variant in variants:
        key = " ".join(text_terms.norm(variant).split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(variant.strip())
    return out


def _llm_variants(question: str, history: list[dict] | None, llm: Any) -> list[str]:
    system = (
        "Reescribes consultas de búsqueda para un expediente judicial. Devuelve SOLO JSON: "
        '{"queries": ["...", "...", "..."]} con tres variantes de búsqueda de la MISMA pregunta. '
        "Usa sinónimos y términos jurídicos, no añadas hechos, cifras, fechas ni nombres nuevos. "
        "No respondas la pregunta."
    )
    context = last_user_question(history)
    user = f"Pregunta: {question}"
    if context:
        user += f"\nPregunta anterior: {context}"
    try:
        result = llm.complete(system, user)
        raw = result if isinstance(result, str) else str(getattr(result, "text", "") or "")
        txt = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        data = json.loads(txt)
    except Exception as exc:  # noqa: BLE001 — el LLM es opcional
        log.warning("query rewrite llm failed: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    queries = data.get("queries")
    if not isinstance(queries, list):
        return []
    source_numbers = _numbers(question) | _numbers(context or "")
    out: list[str] = []
    for item in queries[:_MAX_LLM_VARIANTS]:
        clean = _valid_variant(item, source_numbers)
        if clean:
            out.append(clean)
    return out


def _routing_enabled() -> bool:
    try:
        from app.core.config import get_settings
        settings = get_settings()
        routing = settings.LLM_ROUTING or {}
        return bool(settings.QUERY_REWRITE_ENABLED and "query_rewrite" in routing)
    except Exception:  # noqa: BLE001 — sin configuración no hay LLM
        return False


def _build_router_llm() -> Any | None:
    try:
        from app.providers.llm import get_llm_for_task
        return get_llm_for_task("query_rewrite")
    except Exception as exc:  # noqa: BLE001
        log.warning("query rewrite provider unavailable: %s", exc)
        return None


def rewrite(question: str, history: list[dict] | None = None, llm=None) -> list[str]:
    """Variantes de búsqueda para `question`. La primera es siempre la original."""
    original = (question or "").strip()
    if not original:
        return []

    bases: list[str] = [original]
    previous = last_user_question(history)
    if previous and _is_elliptical(original):
        bases.append(f"{previous} {original}")

    variants: list[str] = []
    expanded = False
    for base in bases:
        variants.append(base)
        synonyms = text_terms.expand_query(base)
        if len(synonyms) > 1:
            expanded = True
        variants.extend(synonyms)

    # La reescritura determinista solo es "insuficiente" cuando no aportó ningún
    # sinónimo. Ahí, y solo ahí, se consulta el router LLM (si está configurado).
    if not expanded and _routing_enabled():
        model = llm if llm is not None else _build_router_llm()
        if model is not None:
            variants.extend(_llm_variants(original, history, model))

    out = _dedupe(variants)[:MAX_VARIANTS]
    return out or [original]


__all__ = ["rewrite", "last_user_question"]
