"""Memoria resumida por sesión de chat (contrato §1.12).

En conversaciones largas no se manda todo el historial: los últimos K turnos
van completos y lo anterior se resume en un bloque estable. El resumen es
determinista (truncado + puntos clave) y solo usa el LLM si `LLM_ROUTING`
define explícitamente la tarea `summary`; si el modelo falla, se conserva el
resumen determinista.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_MAX_CHARS = 1600
_MAX_TURNS = 40
_TURN_CHARS = 280


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s", text)
    return parts[0] if parts else text


def _deterministic(history: list[dict], max_chars: int) -> str:
    lines: list[str] = []
    turns = history[-_MAX_TURNS:]
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        role = "Usuario" if str(turn.get("role")) == "user" else "Asistente"
        text = _clean(turn.get("content"))
        if not text:
            continue
        text = _first_sentence(text) if role == "Asistente" else text
        text = text[:_TURN_CHARS]
        lines.append(f"- {role}: {text}")
    if not lines:
        return ""
    # Los últimos puntos clave son los que más importan: recorta por la izquierda.
    summary = "\n".join(lines)
    return summary[-max_chars:].lstrip()


def summary_llm():
    """Modelo barato de resumen si `LLM_ROUTING.summary` está configurado."""
    try:
        from app.core.config import get_settings
        routing = get_settings().LLM_ROUTING or {}
        if "summary" not in routing:
            return None
        from app.providers.llm import get_llm_for_task
        return get_llm_for_task("summary")
    except Exception as exc:  # noqa: BLE001
        log.debug("summary llm unavailable: %s", exc)
        return None


def _llm_summary(history: list[dict], llm: Any, max_chars: int) -> str | None:
    if llm is None:
        return None
    convo = "\n".join(
        f"{'USUARIO' if str(t.get('role')) == 'user' else 'ASISTENTE'}: {_clean(t.get('content'))[:500]}"
        for t in history[-_MAX_TURNS:] if isinstance(t, dict)
    )
    if not convo:
        return None
    system = (
        "Resume la conversación de un asistente jurídico en 4–8 viñetas: temas tratados, "
        "entidades y decisiones. No inventes datos que no aparezcan. Responde solo con las viñetas."
    )
    user = f"<conversacion>\n{html.escape(convo, quote=False)}\n</conversacion>"
    try:
        result = llm.complete(system, user)
        raw = str(getattr(result, "text", result) or "")
        text = "\n".join(re.sub(r"[ \t]+", " ", line).strip()
                         for line in raw.splitlines() if line.strip())
        return text[:max_chars] or None
    except Exception as exc:  # noqa: BLE001 — el resumen nunca rompe el turno
        log.debug("summary llm failed: %s", exc)
        return None


def summarize(history: list[dict], llm: Any = None, *, max_chars: int = _MAX_CHARS) -> str:
    """Resumen determinista (y opcionalmente LLM) de los turnos anteriores."""
    if not history:
        return ""
    deterministic = _deterministic(history, max_chars)
    if llm is None:
        return deterministic
    return _llm_summary(history, llm, max_chars) or deterministic


def merge_summary(previous: str | None, new_summary: str, max_chars: int = _MAX_CHARS) -> str:
    """Une el resumen previo con el nuevo sin duplicar líneas."""
    parts: list[str] = []
    seen: set[str] = set()
    for block in (previous or "", new_summary or ""):
        for line in block.splitlines():
            key = _clean(line).lower()
            if key and key not in seen:
                seen.add(key)
                parts.append(line.strip())
    return "\n".join(parts)[-max_chars:]
