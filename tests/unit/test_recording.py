"""Record/replay determinista del proveedor LLM."""
from __future__ import annotations

import json
import threading

import pytest

from app.providers.llm import FakeLLM
from app.providers.recording import RecordingMiss, RecordingLLM, record_key, wrap

pytestmark = pytest.mark.unit


def _underlying() -> FakeLLM:
    return FakeLLM("fake-eval")


def test_record_key_es_estable_y_depende_del_prompt():
    a = record_key("system", "user", "fake", "fake-eval")
    assert a == record_key("system", "user", "fake", "fake-eval")
    assert a != record_key("system", "user2", "fake", "fake-eval")
    assert a != record_key("system", "user", "openai", "fake-eval")
    assert len(a) == 64


def test_record_escribe_jsonl_sin_secretos(tmp_path):
    path = tmp_path / "cassettes.jsonl"
    llm = RecordingLLM(_underlying(), mode="record", path=path)
    first = llm.complete("sys", "pregunta")
    second = llm.complete("sys", "otra pregunta")

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["response"] == first.text
    assert lines[1]["response"] == second.text
    raw = path.read_text(encoding="utf-8")
    assert "api_key" not in raw.lower()
    assert "authorization" not in raw.lower()
    assert llm.stats()["recorded"] == 2


def test_replay_es_determinista_y_no_llama_al_proveedor(tmp_path):
    path = tmp_path / "cassettes.jsonl"
    recorder = RecordingLLM(_underlying(), mode="record", path=path)
    original = recorder.complete("sys", "pregunta")

    class ExplodingLLM:
        name = "fake"
        model = "fake-eval"

        def complete(self, system: str, user: str):
            raise AssertionError("no debe llamarse en replay")

    replayer = RecordingLLM(ExplodingLLM(), mode="replay", path=path)
    replayed = replayer.complete("sys", "pregunta")
    assert replayed.text == original.text
    assert replayed.provider == original.provider
    assert replayed.tokens_in == original.tokens_in
    assert replayer.stats()["hits"] == 1


def test_replay_miss_estricto_lanza_y_fallback_usa_proveedor(tmp_path):
    path = tmp_path / "vacio.jsonl"
    path.write_text("", encoding="utf-8")

    strict = RecordingLLM(_underlying(), mode="replay", path=path, on_miss="error")
    with pytest.raises(RecordingMiss):
        strict.complete("sys", "nunca grabada")

    fallback = RecordingLLM(_underlying(), mode="replay", path=path, on_miss="fallback")
    assert fallback.complete("sys", "nunca grabada").text
    assert fallback.stats()["misses"] == 1


def test_wrap_off_devuelve_el_mismo_proveedor():
    base = _underlying()
    assert wrap(base, mode="off") is base


def test_record_es_thread_safe(tmp_path):
    path = tmp_path / "threads.jsonl"
    llm = RecordingLLM(_underlying(), mode="record", path=path)

    def call() -> None:
        for _ in range(5):
            llm.complete("sys", "misma pregunta")

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 40
    assert {json.loads(line)["key"] for line in lines} == {record_key("sys", "misma pregunta", "fake", "fake-eval")}

    replay = RecordingLLM(_underlying(), mode="replay", path=path)
    assert replay.complete("sys", "misma pregunta").text
    assert replay.stats()["hits"] == 1
