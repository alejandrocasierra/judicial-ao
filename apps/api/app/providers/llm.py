from __future__ import annotations

import json
import re
from typing import Callable

import httpx

from app.core.config import get_settings
from app.providers.base import LLMResult


class FakeLLM:
    """Proveedor determinista para dev/test (prohibido en producción por config).
    Cita la primera evidencia con su primera oración. `script` permite a las pruebas
    simular respuestas maliciosas o inválidas del modelo."""
    name = "fake"
    script: Callable[[str, str], str] | None = None

    def __init__(self, model: str):
        self.model = model

    def complete(self, system: str, user: str) -> LLMResult:
        if FakeLLM.script:
            out = FakeLLM.script(system, user)
        else:
            m = re.search(r'<evidence id="(E\d+)"[^>]*>\s*(.*?)</evidence>', user, re.S)
            if not m:
                out = json.dumps({"claims": [], "uncertainties": ["no evidence"]})
            else:
                sentence = re.split(r"(?<=[.!?])\s", m.group(2).strip())[0][:400]
                out = json.dumps({"claims": [{"text": sentence, "citations": [m.group(1)]}], "uncertainties": []},
                                 ensure_ascii=False)
        return LLMResult(out, self.name, self.model, len(user) // 4, len(out) // 4)


def _messages_url(base: str) -> str:
    """Endpoint de Messages de Anthropic a partir de la base configurada.
    Si la base ya trae la versión (…/v1) no se duplica."""
    b = (base or "").rstrip("/")
    return f"{b}/messages" if re.search(r"/(v\d+[a-z]*)$", b) else f"{b}/v1/messages"


def _chat_completions_url(base: str) -> str:
    """Endpoint de Chat Completions (compatible OpenAI) a partir de la base.
    Soporta bases que ya incluyen versión o el mount de Gemini (…/v1beta/openai)."""
    b = (base or "").rstrip("/")
    if re.search(r"/(v\d+[a-z]*|openai)$", b) or "/openai/" in b:
        return f"{b}/chat/completions"
    return f"{b}/v1/chat/completions"


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 api_base_url: str | None = None, api_version: str | None = None):
        s = get_settings()
        self.model = model or s.LLM_MODEL
        self.api_key = api_key or s.LLM_API_KEY
        self.api_base_url = api_base_url or s.LLM_API_BASE_URL
        self.api_version = api_version or s.LLM_API_VERSION
        self.timeout = s.LLM_TIMEOUT_SECONDS
        self.max_tokens = s.LLM_MAX_TOKENS

    def complete(self, system: str, user: str) -> LLMResult:
        r = httpx.post(_messages_url(self.api_base_url), timeout=self.timeout,
                       headers={"x-api-key": self.api_key, "anthropic-version": self.api_version,
                                "content-type": "application/json"},
                       json={"model": self.model, "max_tokens": self.max_tokens, "system": system,
                             "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        d = r.json()
        text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        u = d.get("usage", {})
        return LLMResult(text, self.name, d.get("model", self.model), u.get("input_tokens", 0), u.get("output_tokens", 0))


class OpenAiLLM:
    """Adaptador para APIs compatibles con OpenAI Chat Completions
    (OpenAI, Azure OpenAI, proxies locales, etc.)."""
    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 api_base_url: str | None = None):
        s = get_settings()
        self.model = model or s.LLM_MODEL
        self.api_key = api_key or s.LLM_API_KEY
        self.api_base_url = api_base_url or s.LLM_API_BASE_URL
        self.timeout = s.LLM_TIMEOUT_SECONDS
        self.max_tokens = s.LLM_MAX_TOKENS

    def complete(self, system: str, user: str) -> LLMResult:
        url = _chat_completions_url(self.api_base_url)
        headers = {"authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        r = httpx.post(url, timeout=self.timeout, headers=headers,
                       json={"model": self.model, "max_tokens": self.max_tokens, "messages": messages})
        if r.status_code == 400 and "max_tokens" in r.text:
            r = httpx.post(url, timeout=self.timeout, headers=headers,
                           json={"model": self.model, "max_completion_tokens": self.max_tokens,
                                 "messages": messages})
        r.raise_for_status()
        d = r.json()
        text = d["choices"][0].get("message", {}).get("content", "") if d.get("choices") else ""
        u = d.get("usage", {})
        return LLMResult(text, self.name, d.get("model", self.model), u.get("prompt_tokens", 0), u.get("completion_tokens", 0))


def get_llm():
    s = get_settings()
    if s.LLM_PROVIDER == "fake":
        return FakeLLM(s.LLM_MODEL)
    if s.LLM_PROVIDER == "openai":
        return OpenAiLLM()
    return AnthropicLLM()


def _provider_base_url(s, provider: str) -> str:
    """Base URL del API (compatible con OpenAI Chat Completions) por proveedor configurado.
    Las URLs viven en el entorno (Settings), nunca quemadas en el código."""
    return {"openai": s.OPENAI_BASE_URL, "deepseek": s.DEEPSEEK_BASE_URL,
            "kimi": s.KIMI_BASE_URL, "gemini": s.GEMINI_BASE_URL}.get(provider, s.LLM_API_BASE_URL)


def get_llm_for_model(model_id: str, org_id: str, user_id: str):
    """Construye un proveedor LLM a partir de un modelo configurado en /dashboard/models.

    Si el modelo no existe o no pertenece a la organización, cae al modelo por defecto.
    Los proveedores compatibles con OpenAI (OpenAI, Gemini, Kimi, DeepSeek, custom)
    usan el adaptador OpenAI con la base URL propia de cada proveedor.
    """
    from app.core.db import one, tx

    with tx(org_id, user_id) as c:
        m = one(c, "SELECT provider, model_name, encrypted_api_key, api_base_url FROM ai_models WHERE id = :i", i=model_id)
    if not m:
        return get_llm()

    s = get_settings()
    provider = m["provider"]
    model = m["model_name"]
    api_key = m["encrypted_api_key"] or s.LLM_API_KEY
    # URL base: override del modelo (proxy/VPS/región) o la del proveedor en el entorno.
    api_base_url = m.get("api_base_url") or _provider_base_url(s, provider)
    api_version = s.LLM_API_VERSION

    if provider == "fake":
        return FakeLLM(model)
    if provider in ("openai", "gemini", "kimi", "deepseek", "custom"):
        return OpenAiLLM(model=model, api_key=api_key, api_base_url=api_base_url)
    return AnthropicLLM(model=model, api_key=api_key, api_base_url=api_base_url, api_version=api_version)


def get_llm_for_task(task: str):
    """Routing por tarea (D4): permite usar modelos económicos para NER/clasificación
    y frontier para razonamiento/contradicciones.

    Soporta TODOS los proveedores compatibles con OpenAI (openai, gemini, kimi, deepseek,
    custom) — igual que `get_llm_for_model` — y Anthropic. Si el proveedor no trae
    `api_base_url` explícito, se usa la URL base del proveedor del entorno."""
    s = get_settings()
    routing = s.LLM_ROUTING or {}
    cfg = routing.get(task, {})
    provider = cfg.get("provider", s.LLM_PROVIDER)
    model = cfg.get("model", s.LLM_MODEL)
    api_key = cfg.get("api_key") or s.LLM_API_KEY
    api_base_url = cfg.get("api_base_url") or _provider_base_url(s, provider)
    api_version = cfg.get("api_version") or s.LLM_API_VERSION
    if provider == "fake":
        return FakeLLM(model)
    if provider in ("openai", "gemini", "kimi", "deepseek", "custom"):
        return OpenAiLLM(model=model, api_key=api_key, api_base_url=api_base_url)
    return AnthropicLLM(model=model, api_key=api_key, api_base_url=api_base_url, api_version=api_version)
