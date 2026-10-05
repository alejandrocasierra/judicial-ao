"""UT-AGT — agent tools allowlist."""
from unittest.mock import MagicMock

import pytest

from app.services import agent_tools

pytestmark = pytest.mark.unit


def test_ut_agt_01_ilike_terms():
    clause, params = agent_tools._ilike_terms("demanda ejecutiva", column="text")
    assert "text ILIKE :t0" in clause
    assert "text ILIKE :t1" in clause
    assert params["t0"] == "%demanda%"
    assert params["t1"] == "%ejecutiva%"


def test_ut_agt_02_ilike_terms_short_words():
    clause, params = agent_tools._ilike_terms("el la de", column="text")
    assert clause == ""
    assert params == {}


def test_ut_agt_03_execute_dispatch(monkeypatch):
    called = {}

    def fake_legacy_retrieve(_conn, _case, query, k=None):
        called["legacy"] = (query, k)
        return [{"handle": "E1", "source_type": "document_page", "text": "x"},
                {"handle": "E2", "source_type": "document_page", "text": "y"},
                {"handle": "E3", "source_type": "document_page", "text": "z"},
                {"handle": "E4", "source_type": "document_page", "text": "w"}]

    monkeypatch.setattr(agent_tools.answering, "legacy_retrieve", fake_legacy_retrieve)
    result = agent_tools.execute(MagicMock(), "case", "search_documents", {"query": "demanda", "k": 3})
    assert called["legacy"] == ("demanda", None)
    assert len(result) == 3
    assert result[0]["handle"] == "E1"


def test_ut_agt_04_unknown_tool():
    with pytest.raises(ValueError, match="unknown tool"):
        agent_tools.execute(MagicMock(), "case", "not_a_tool", {})
