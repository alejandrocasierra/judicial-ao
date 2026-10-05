"""UT-EMB — proveedores de embeddings."""
import math

import pytest

from app.core.config import Settings
from app.providers import embeddings as emb

pytestmark = pytest.mark.unit


class _Settings:
    EMBEDDING_DIMENSIONS = 8
    EMBEDDING_MAX_CHARS = 100
    EMBEDDING_MODEL = "fake-model"
    EMBEDDING_BATCH_SIZE = 2


def test_ut_emb_01_fake_returns_unit_vectors():
    p = emb.FakeEmbedding(_Settings())
    vecs = p.embed(["hola", "mundo"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 8
    for v in vecs:
        assert math.isclose(sum(x * x for x in v), 1.0, abs_tol=1e-6)


def test_ut_emb_02_fake_is_deterministic():
    p = emb.FakeEmbedding(_Settings())
    assert p.embed(["x"]) == p.embed(["x"])


def test_ut_emb_03_fake_respects_max_chars():
    p = emb.FakeEmbedding(_Settings())
    long_text = "a" * 200
    v1 = p.embed([long_text])[0]
    v2 = p.embed([long_text[:100]])[0]
    assert v1 == v2


def test_ut_emb_04_factory_uses_config(monkeypatch):
    s = Settings(_env_file=None)
    monkeypatch.setattr(s, "EMBEDDING_PROVIDER", "fake")
    monkeypatch.setattr(s, "EMBEDDING_DIMENSIONS", 16)
    monkeypatch.setattr(s, "EMBEDDING_MAX_CHARS", 50)
    p = emb.get_embedding_provider(s)
    assert p.name == "fake"
    assert p.dimensions == 16


def test_ut_emb_05_unknown_provider_raises():
    class S:
        EMBEDDING_PROVIDER = "unknown"
    with pytest.raises(ValueError):
        emb.get_embedding_provider(S())
