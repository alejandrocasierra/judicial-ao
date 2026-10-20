"""UT-SKL — skills ejecutables: prompt fusionado, allowlist y orden estable."""
from __future__ import annotations

import pytest

from app.services import skills

pytestmark = pytest.mark.unit


def test_ut_skl_01_no_agent_returns_defaults():
    assert skills.load_config(None, None) == {"prompt": None, "allowed_tools": None, "parameters": {}}


def test_ut_skl_02_missing_agent_returns_defaults(monkeypatch):
    monkeypatch.setattr(skills.db, "one", lambda *_a, **_k: None)
    assert skills.load_config(object(), "agent-1")["prompt"] is None


def test_ut_skl_03_prompt_merges_agent_and_skills_in_order(monkeypatch):
    monkeypatch.setattr(skills.db, "one", lambda *_a, **_k: {"system_prompt": "BASE", "skills": ["s1", "s2"]})
    monkeypatch.setattr(skills.db, "rows", lambda *_a, **_k: [
        {"name": "Primera", "system_prompt": "P1", "tools": [], "parameters": {}},
        {"name": "Segunda", "system_prompt": "P2", "tools": [], "parameters": {}},
    ])
    config = skills.load_config(object(), "agent-1")
    assert config["prompt"].index("BASE") < config["prompt"].index("[Skill: Primera]") \
        < config["prompt"].index("[Skill: Segunda]")


def test_ut_skl_04_allowed_tools_union_and_parameters(monkeypatch):
    monkeypatch.setattr(skills.db, "one", lambda *_a, **_k: {"system_prompt": "", "skills": ["s1", "s2"]})
    monkeypatch.setattr(skills.db, "rows", lambda *_a, **_k: [
        {"name": "A", "system_prompt": "PA", "tools": ["search_case"], "parameters": {"depth": 2}},
        {"name": "B", "system_prompt": "PB", "tools": ["locate"], "parameters": {"k": 10}},
    ])
    config = skills.load_config(object(), "agent-1")
    assert config["allowed_tools"] == {"search_case", "locate"}
    assert config["parameters"] == {"depth": 2, "k": 10}


def test_ut_skl_05_empty_tools_means_no_restriction(monkeypatch):
    monkeypatch.setattr(skills.db, "one", lambda *_a, **_k: {"system_prompt": "BASE", "skills": ["s1"]})
    monkeypatch.setattr(skills.db, "rows", lambda *_a, **_k: [
        {"name": "A", "system_prompt": "PA", "tools": [], "parameters": {}},
    ])
    assert skills.load_config(object(), "agent-1")["allowed_tools"] is None


def test_ut_skl_06_validate_tool_enforces_allowlist():
    ok, reason = skills.validate_tool("search_case", {"search_case"})
    assert ok and reason is None
    ok, reason = skills.validate_tool("locate", {"search_case"})
    assert not ok and "not allowed" in reason
    ok, _ = skills.validate_tool("__nope__", None)
    assert not ok
