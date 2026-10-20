"""UT-CIR — circuit breaker de proveedores."""
from __future__ import annotations

import httpx
import pytest

from app.providers import circuit

pytestmark = pytest.mark.unit


def test_ut_cir_01_success_resets_failures():
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return "ok"

    assert circuit.call("p:m", ok) == "ok"
    assert circuit.call("p:m", ok) == "ok"
    assert calls["n"] == 2
    assert not circuit.is_open("p:m")


def test_ut_cir_02_opens_after_threshold_and_fails_fast():
    calls = {"n": 0}

    def timeout():
        calls["n"] += 1
        raise httpx.TimeoutException("slow")

    for _ in range(3):
        with pytest.raises(httpx.TimeoutException):
            circuit.call("p:m", timeout, threshold=3, cooldown=60)
    assert circuit.is_open("p:m")
    with pytest.raises(circuit.CircuitOpenError):
        circuit.call("p:m", timeout, threshold=3, cooldown=60)
    # La cuarta llamada ni siquiera se intentó.
    assert calls["n"] == 3


def test_ut_cir_03_contract_errors_do_not_trip():
    def bad_json():
        raise ValueError("json inválido")

    for _ in range(4):
        with pytest.raises(ValueError):
            circuit.call("p:m", bad_json, threshold=2, cooldown=60)
    assert not circuit.is_open("p:m")


def test_ut_cir_04_reset_allows_calls_again():
    def timeout():
        raise httpx.ConnectError("down")

    for _ in range(2):
        with pytest.raises(httpx.ConnectError):
            circuit.call("p:m", timeout, threshold=2, cooldown=60)
    assert circuit.is_open("p:m")
    circuit.reset()
    assert not circuit.is_open("p:m")
    assert circuit.call("p:m", lambda: 1) == 1
