"""UT-VRF — verificación semántica anti-alucinación."""
from types import SimpleNamespace

import pytest

from app.services import verification

pytestmark = pytest.mark.unit


def test_ut_vrf_01_supported_claim(monkeypatch):
    class FakeLLM:
        def complete(self, _system, _user):
            return SimpleNamespace(
                text='{"verdicts": [{"claim": "Se presentó demanda.", "status": "supported", "reason": "texto lo dice", "citations": ["E1"]}]}',
                provider="fake", model="fake", tokens_in=1, tokens_out=1,
            )
    monkeypatch.setattr(verification, "get_llm", lambda: FakeLLM())
    claims = [{"text": "Se presentó demanda.", "citations": ["E1"]}]
    evidence = [{"handle": "E1", "source_type": "document_page", "text": "se presentó demanda ejecutiva"}]
    verdicts = verification.verify(claims, evidence)
    assert verdicts[0]["status"] == "supported"


def test_ut_vrf_02_bad_json_returns_empty(monkeypatch):
    class FakeLLM:
        def complete(self, _system, _user):
            return SimpleNamespace(text="no json", provider="fake", model="fake", tokens_in=1, tokens_out=1)
    monkeypatch.setattr(verification, "get_llm", lambda: FakeLLM())
    assert verification.verify([{"text": "x", "citations": ["E1"]}], []) == []
