"""Juez semántico portable: parseo, mayoría, judge_error y same_family."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts.eval_matrix import _extract_json, _majority, judge

pytestmark = pytest.mark.unit

VALID = {
    "faithfulness": 5,
    "citation_correctness": 4,
    "usefulness": 5,
    "naturalness": 4,
    "abstention": 5,
    "verdict": "pass",
    "reasons": ["la cita respalda la afirmación"],
}


class StubLLM:
    def __init__(self, texts: list[str], name: str = "openai", model: str = "judge-model"):
        self.texts = texts
        self.name = name
        self.model = model
        self.calls = 0

    def complete(self, system: str, user: str):
        text = self.texts[self.calls % len(self.texts)]
        self.calls += 1
        return SimpleNamespace(text=text)


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def test_extract_json_tolera_fences_y_prosa():
    raw = "```json\n" + _json(VALID) + "\n```"
    assert _extract_json(raw)["verdict"] == "pass"
    assert _extract_json("Claro. " + _json(VALID) + " Fin.")["faithfulness"] == 5
    assert _extract_json("sin json") is None
    assert _extract_json("") is None


def test_judge_mayoria_de_tres_muestras():
    low = dict(VALID, faithfulness=2, verdict="fail")
    llm = StubLLM([_json(VALID), _json(VALID), _json(low)])
    out = judge("El demandado pagó.", "El demandado pagó $80.000.000.", llm, samples=3)
    assert out["judge_error"] is False
    assert out["faithfulness"] == 5
    assert out["verdict"] == "pass"
    assert llm.calls == 3


def test_judge_error_no_promedia():
    llm = StubLLM(["no soy json", "{roto", "{}"])
    out = judge("claim", "evidence", llm, samples=3)
    assert out["judge_error"] is True
    assert out["faithfulness"] is None
    assert out["verdict"] is None


def test_judge_dos_muestras_validas_bastan():
    llm = StubLLM([_json(VALID), "basura", _json(dict(VALID, usefulness=3))])
    out = judge("claim", "evidence", llm, samples=3)
    assert out["judge_error"] is False
    assert out["usefulness"] == 4  # mediana de 5 y 3
    assert out["agreement"] == 1.0


def test_judge_same_family_cuando_repites_generador():
    llm = StubLLM([_json(VALID)] * 3, name="openai", model="gpt-4o-mini")
    out = judge("claim", "evidence", llm, generator="gpt-4o-mini")
    assert out["same_family"] is True
    out2 = judge("claim", "evidence", llm, generator="gemini-2.5-flash")
    assert out2["same_family"] is False


def test_judge_heuristico_offline_detecta_cifra_inventada():
    out = judge("El demandado pagó $99.000.000.", "El demandado pagó $80.000.000.", None)
    assert out["judge_mode"] == "heuristic"
    assert out["faithfulness"] <= 2
    assert out["verdict"] == "fail"


def test_majority_numerica_y_categorica():
    assert _majority([5, 5, 3]) == 5
    assert _majority([4, 4, 3, 3]) == 4
    assert _majority(["pass", "pass", "fail"]) == "pass"
    assert _majority([]) is None
