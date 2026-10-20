"""UT-MEM — memoria resumida de la conversación."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import memory

pytestmark = pytest.mark.unit


def _history(n: int) -> list[dict]:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"pregunta {i} sobre el pago"})
        out.append({"role": "assistant", "content": f"respuesta {i}. Detalle adicional que no cabe."})
    return out


def test_ut_mem_01_empty_history_is_empty_summary():
    assert memory.summarize([]) == ""


def test_ut_mem_02_deterministic_summary_has_roles_and_content():
    summary = memory.summarize(_history(3))
    assert "Usuario: pregunta 0" in summary
    assert "Asistente: respuesta 0." in summary
    assert "Detalle adicional" not in summary.splitlines()[1]


def test_ut_mem_03_summary_is_capped():
    summary = memory.summarize(_history(40), max_chars=200)
    assert len(summary) <= 200


def test_ut_mem_04_llm_summary_is_used_when_available():
    class FakeLLM:
        def complete(self, _system, _user):
            return SimpleNamespace(text="- punto uno\n- punto dos")
    assert memory.summarize(_history(2), llm=FakeLLM()) == "- punto uno\n- punto dos"


def test_ut_mem_05_llm_failure_falls_back_to_deterministic():
    class BrokenLLM:
        def complete(self, _system, _user):
            raise RuntimeError("provider down")
    summary = memory.summarize(_history(2), llm=BrokenLLM())
    assert "Usuario: pregunta" in summary


def test_ut_mem_06_merge_summary_does_not_duplicate_lines():
    merged = memory.merge_summary("- a\n- b", "- b\n- c")
    assert merged.count("- b") == 1
    assert "- a" in merged and "- c" in merged
