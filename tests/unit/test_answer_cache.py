"""La clave de respuesta repetida no debe reutilizar una respuesta de otro contexto."""
from types import SimpleNamespace

import pytest

from app.routers import query as query_router

pytestmark = pytest.mark.unit


def _body(question="¿Quiénes son los demandados?", **extra):
    base = {"question": question, "model_id": "model-1", "agent_id": "agent-1",
            "strategy": "rag", "attachments": []}
    base.update(extra)
    return SimpleNamespace(**base)


def _settings():
    return SimpleNamespace(ANSWER_CACHE_TTL_SECONDS=3600, PIPELINE_VERSION="pipeline-1")


def test_same_standalone_question_same_key(monkeypatch):
    monkeypatch.setattr(query_router, "one", lambda *_a, **_k: {"docs": 1})
    p = SimpleNamespace(user_id="user-1")
    key1 = query_router._answer_cache_key(
        None, "case-1", _body(), p, _settings(), [], "es", None, None)
    key2 = query_router._answer_cache_key(
        None, "case-1", _body(), p, _settings(), [], "es", None, None)
    assert key1 and key1 == key2


def test_different_model_question_or_attachment_does_not_reuse(monkeypatch):
    monkeypatch.setattr(query_router, "one", lambda *_a, **_k: {"docs": 1})
    p = SimpleNamespace(user_id="user-1")
    base = query_router._answer_cache_key(
        None, "case-1", _body(), p, _settings(), [], "es", None, None)
    other_model = query_router._answer_cache_key(
        None, "case-1", _body(model_id="model-2"), p, _settings(), [], "es", None, None)
    other_question = query_router._answer_cache_key(
        None, "case-1", _body("¿Quiénes son los demandantes?"), p, _settings(), [], "es", None, None)
    attached = query_router._answer_cache_key(
        None, "case-1", _body(attachments=[{"kind": "document", "id": "d1"}]),
        p, _settings(), [], "es", None, None)
    assert base != other_model
    assert base != other_question
    assert attached is None


def test_contextual_question_depends_on_history(monkeypatch):
    monkeypatch.setattr(query_router, "one", lambda *_a, **_k: {"docs": 1})
    p = SimpleNamespace(user_id="user-1")
    first = query_router._answer_cache_key(
        None, "case-1", _body("¿Y el demandado?"), p, _settings(),
        [{"role": "user", "content": "partes"}], "es", None, None)
    second = query_router._answer_cache_key(
        None, "case-1", _body("¿Y el demandado?"), p, _settings(),
        [{"role": "user", "content": "otra cosa"}], "es", None, None)
    assert first != second
