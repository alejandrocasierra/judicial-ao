"""STATIC: los JSON Schemas del Case Knowledge Package son válidos y los prompts están versionados."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = sorted((ROOT / "packages" / "schemas").glob("*.schema.json"))


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.name)
def test_static_sch_01_schema_is_valid(path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema.get("$id") and schema.get("title")


def test_static_sch_02_answer_schema_accepts_platform_output():
    schema = json.loads((ROOT / "packages" / "schemas" / "answer.schema.json").read_text(encoding="utf-8"))
    sample = {"answer": "x", "claims": [], "citations": [], "unsupported_claims": [], "uncertainties": [],
              "related_contradictions": [], "knowledge_type": "case", "evidence_count": 0}
    errors = list(Draft202012Validator(schema).iter_errors(sample))
    assert errors == [], [e.message for e in errors]


def test_static_sch_03_prompts_are_versioned_and_declare_untrusted_content():
    prompts = list((ROOT / "packages" / "prompts").glob("*.v*.md"))
    assert prompts
    for p in prompts:
        t = p.read_text(encoding="utf-8")
        assert "UNTRUSTED" in t and "{{LOCALE}}" in t
