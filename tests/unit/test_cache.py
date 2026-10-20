"""UT-CCH — caché LRU con TTL y borrado por prefijo (sin Redis en tests)."""
from __future__ import annotations

import time

import pytest

from app.services import cache

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean():
    cache.clear_memory()
    yield
    cache.clear_memory()


def test_ut_cch_01_set_and_get_json():
    cache.set_json("retrieval", "k1", {"items": [1, 2]}, 60)
    assert cache.get_json("retrieval", "k1") == {"items": [1, 2]}


def test_ut_cch_02_missing_key_returns_none():
    assert cache.get_json("retrieval", "nope") is None


def test_ut_cch_03_ttl_expires_entry():
    cache.set_json("retrieval", "k1", {"v": 1}, 1)
    assert cache.get_json("retrieval", "k1") == {"v": 1}
    time.sleep(1.05)
    assert cache.get_json("retrieval", "k1") is None


def test_ut_cch_04_delete_prefix_only_removes_namespace():
    cache.set_json("retrieval", "a", 1, 60)
    cache.set_json("retrieval", "b", 2, 60)
    cache.set_json("embeddings", "a", 3, 60)
    cache.delete_prefix("retrieval")
    assert cache.get_json("retrieval", "a") is None
    assert cache.get_json("retrieval", "b") is None
    assert cache.get_json("embeddings", "a") == 3


def test_ut_cch_05_delete_prefix_is_idempotent():
    cache.delete_prefix("retrieval")
    cache.delete_prefix("retrieval")


def test_ut_cch_06_values_are_json_safe():
    from datetime import datetime
    cache.set_json("retrieval", "k", {"at": datetime(2026, 1, 1, 10, 0)}, 60)
    assert cache.get_json("retrieval", "k")["at"]
