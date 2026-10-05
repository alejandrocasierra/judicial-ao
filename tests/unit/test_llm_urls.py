"""UT-LLM — construcción de endpoints por proveedor (cualquier IA con cualquier API key)."""
from __future__ import annotations

import pytest

from app.providers.llm import _chat_completions_url, _messages_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("base,expected", [
    ("https://api.openai.com", "https://api.openai.com/v1/chat/completions"),
    ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.moonshot.cn", "https://api.moonshot.cn/v1/chat/completions"),
    ("https://api.openai.com/", "https://api.openai.com/v1/chat/completions"),
    # Gemini ya trae el mount OpenAI: no se duplica el /v1
    ("https://generativelanguage.googleapis.com/v1beta/openai",
     "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    # proxy/base versionada del usuario
    ("https://mi-proxy.example.com/v1", "https://mi-proxy.example.com/v1/chat/completions"),
    ("https://mi-proxy.example.com", "https://mi-proxy.example.com/v1/chat/completions"),
    ("http://localhost:11434/v1", "http://localhost:11434/v1/chat/completions"),
])
def test_ut_llm_01_chat_completions_url(base, expected):
    assert _chat_completions_url(base) == expected


@pytest.mark.parametrize("base,expected", [
    ("https://api.anthropic.com", "https://api.anthropic.com/v1/messages"),
    ("https://api.anthropic.com/", "https://api.anthropic.com/v1/messages"),
    ("https://mi-proxy.example.com/v1", "https://mi-proxy.example.com/v1/messages"),
    ("https://proxy/v1beta", "https://proxy/v1beta/messages"),
])
def test_ut_llm_02_messages_url(base, expected):
    assert _messages_url(base) == expected
