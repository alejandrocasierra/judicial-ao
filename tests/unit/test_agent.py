"""UT-AGN — agent loop y síntesis final."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import agent

pytestmark = pytest.mark.unit


def _make_llm(turns):
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, _system, _user):
            txt = turns[self.calls]
            self.calls += 1
            return SimpleNamespace(text=txt, provider="fake", model="fake", tokens_in=1, tokens_out=1)
    return FakeLLM()


def test_ut_agn_01_tool_loop_then_synthesis(monkeypatch):
    tool_calls = [
        '{"thought": "buscar docs", "tool": "search_documents", "arguments": {"query": "demanda"}}',
        '{"thought": "suficiente", "done": true}',
    ]
    final_answer = '{"claims": [{"text": "Se presentó demanda.", "citations": ["E1"]}], "uncertainties": []}'
    llm = _make_llm(tool_calls + [final_answer])
    monkeypatch.setattr(agent.get_llm, "__call__", lambda: llm)  # get_llm es función; reemplazamos el retorno
    monkeypatch.setattr(agent, "get_llm", lambda: llm)

    def fake_execute(_conn, _case, tool, arguments):
        return [{"handle": "E1", "source_type": "document_page", "document_id": "d1",
                 "page_number": 1, "text": "Se presentó demanda ejecutiva."}]

    monkeypatch.setattr(agent.agent_tools, "execute", fake_execute)

    conn = MagicMock()
    result = agent.run_agent_query(conn, "case", "¿Se presentó demanda?", "es")
    assert result["evidence_count"] == 1
    assert any(cl["text"] == "Se presentó demanda." for cl in result["claims"])
    assert not result["unsupported_claims"]


def test_ut_agn_02_no_evidence(monkeypatch):
    class FakeLLM:
        def complete(self, _system, _user):
            return SimpleNamespace(text='{"done": true}', provider="fake", model="fake", tokens_in=1, tokens_out=1)
    monkeypatch.setattr(agent, "get_llm", lambda: FakeLLM())
    monkeypatch.setattr(agent.agent_tools, "execute", lambda _c, _case, _t, _a: [])
    conn = MagicMock()
    result = agent.run_agent_query(conn, "case", "¿Se presentó demanda?", "es")
    assert result["evidence_count"] == 0
