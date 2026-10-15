"""UT-ASP — agent system prompt versionado y Process Graph."""
from pathlib import Path

import pytest

from app.services import builtin_agents
from app.services.case_tools import TOOL_REGISTRY

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def test_ut_asp_01_default_agent_version_is_v7():
    """builtin_agents apunta explícitamente a la versión 7 del system prompt."""
    assert builtin_agents.DEFAULT_AGENT_VERSION == 7


def test_ut_asp_02_v7_prompt_contains_process_graph_instructions():
    """El prompt v7 instruye al agente a usar process_path/event_relations primero y a citar EV-XXXX."""
    path = ROOT / "packages" / "prompts" / f"agent_system.v{builtin_agents.DEFAULT_AGENT_VERSION}.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "process_path" in text
    assert "event_relations" in text
    assert "EV-XXXX" in text
    assert "Process Graph" in text
    assert "Process Graph tools FIRST" in text


def test_ut_asp_03_process_graph_tools_are_registered():
    """Las tools process_path y event_relations están en el registro de tools del caso."""
    assert "process_path" in TOOL_REGISTRY
    assert "event_relations" in TOOL_REGISTRY
