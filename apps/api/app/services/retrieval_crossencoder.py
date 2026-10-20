"""Cross-encoder local opcional (contrato §1.4, fase 3).

Segunda pasada de ranking con `sentence-transformers.CrossEncoder`. Es una
dependencia OPCIONAL: si no está instalada, si el modelo no se puede cargar o
si la inferencia falla, se devuelven los items intactos y el reranker
determinista sigue mandando.

El modelo se carga una sola vez por proceso y solo cuando `rerank` se invoca;
el llamador decide si lo usa (`RERANK_CROSSENCODER` en `Settings`).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_model: Any | None = None
_load_failed = False


def _load_model() -> Any | None:
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None
    try:
        from sentence_transformers import CrossEncoder
    except Exception as exc:  # noqa: BLE001 — dependencia opcional
        log.info("cross-encoder unavailable: %s", exc)
        _load_failed = True
        return None
    try:
        from app.core.config import get_settings
        name = get_settings().RERANK_CROSSENCODER_MODEL
        log.info("loading cross-encoder model=%s", name)
        _model = CrossEncoder(name)
    except Exception as exc:  # noqa: BLE001 — modelo ausente o sin recursos
        log.warning("cross-encoder load failed: %s", exc)
        _load_failed = True
        _model = None
    return _model


def available() -> bool:
    """True si la dependencia está instalada (no implica que el modelo cargue)."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def rerank(question: str, items: list[dict], top_k: int) -> list[dict]:
    """Reordena `items` con el cross-encoder. Fallback: devuelve `items`."""
    if not items:
        return items
    model = _load_model()
    if model is None:
        return items
    pairs = [[question, str(item.get("text") or "")] for item in items]
    try:
        scores = model.predict(pairs)
    except Exception as exc:  # noqa: BLE001 — nunca rompe el turno
        log.warning("cross-encoder inference failed: %s", exc)
        return items

    ranked: list[tuple[int, dict]] = []
    for index, (item, score) in enumerate(zip(items, scores, strict=False)):
        copy = dict(item)
        try:
            copy["rerank_score"] = float(score)
        except (TypeError, ValueError):
            copy["rerank_score"] = 0.0
        ranked.append((index, copy))
    ranked.sort(key=lambda pair: (-pair[1]["rerank_score"], pair[0]))
    ordered = [item for _, item in ranked]
    return ordered[:top_k] if top_k and top_k > 0 else ordered


def reset() -> None:
    """Libera el modelo cacheado (pruebas y cambios de configuración)."""
    global _model, _load_failed
    _model = None
    _load_failed = False


__all__ = ["available", "rerank", "reset"]
