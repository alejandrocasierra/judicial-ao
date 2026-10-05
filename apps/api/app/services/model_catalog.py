"""Catálogo DINÁMICO de modelos por proveedor.

Consulta la lista REAL de modelos de cada proveedor (endpoint `/models`) para no
depender de una lista curada a mano: así, cada vez que el proveedor lanza un modelo,
aparece solo en el selector. Si no hay API key o el proveedor no responde, el llamador
cae al catálogo estático.

Las URLs base viven en `Settings` (nunca quemadas), igual que en `providers/llm.py`.
"""
from __future__ import annotations

import logging
import re

import httpx

from app.core.config import get_settings
from app.providers.llm import _provider_base_url

log = logging.getLogger(__name__)


def _openai_models_url(base: str) -> str:
    """Endpoint /models compatible con OpenAI a partir de la base configurada."""
    b = (base or "").rstrip("/")
    if b.endswith("/openai") or re.search(r"/v\d+[a-z]*$", b):
        return f"{b}/models"
    return f"{b}/v1/models"


def _anthropic_models_url(base: str) -> str:
    b = (base or "").rstrip("/")
    return f"{b}/models" if re.search(r"/v\d+[a-z]*$", b) else f"{b}/v1/models"


def _gemini_models_url(base: str) -> str:
    """El listado nativo de Gemini vive en {base}/models; si la base apunta al mount
    compatible con OpenAI (…/v1beta/openai) se retira ese sufijo."""
    b = (base or "").rstrip("/")
    if b.endswith("/openai"):
        b = b[: -len("/openai")]
    return f"{b}/models" if re.search(r"/v\d+[a-z]*$", b) else f"{b}/v1beta/models"


def _is_chat_model(model_id: str) -> bool:
    """Descarta modelos que no son de chat (embeddings, TTS, STT, imágenes, video, live…)."""
    low = (model_id or "").lower()
    return not any(x in low for x in (
        "embedding", "tts", "whisper", "dall-e", "davinci", "babbage", "moderation",
        "realtime", "transcribe", "speech", "audio", "image", "sora",
        "veo", "lyria", "robotics", "computer-use", "native-audio", "live",
        "antigravity", "deep-research", "aqa"))


def list_provider_models(provider: str, api_key: str, base_url: str | None = None) -> list[str]:
    """Lista los modelos reales del proveedor. Lanza excepción si el proveedor falla."""
    s = get_settings()
    base = base_url or _provider_base_url(s, provider)
    timeout = s.LLM_TIMEOUT_SECONDS
    if provider in ("fake", None):
        return []
    if provider == "anthropic":
        r = httpx.get(_anthropic_models_url(base), timeout=timeout, params={"limit": 1000},
                      headers={"x-api-key": api_key, "anthropic-version": s.LLM_API_VERSION})
        r.raise_for_status()
        return sorted({m["id"] for m in r.json().get("data", []) if m.get("id")})
    if provider == "gemini":
        r = httpx.get(_gemini_models_url(base), timeout=timeout, params={"pageSize": 1000},
                      headers={"x-goog-api-key": api_key})
        r.raise_for_status()
        out: set[str] = set()
        for m in r.json().get("models", []):
            name = (m.get("name") or "").removeprefix("models/")
            methods = m.get("supportedGenerationMethods") or []
            if name and _is_chat_model(name) and (not methods or "generateContent" in methods):
                out.add(name)
        return sorted(out)
    # OpenAI-compatible: openai, deepseek, kimi, custom
    r = httpx.get(_openai_models_url(base), timeout=timeout,
                  headers={"authorization": f"Bearer {api_key}"})
    r.raise_for_status()
    return sorted({m["id"] for m in r.json().get("data", []) if m.get("id") and _is_chat_model(m["id"])})
