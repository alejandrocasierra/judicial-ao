"""UT-DLN — presupuesto de latencia y degradación."""
from __future__ import annotations

import time

import pytest

from app.services.deadline import Deadline

pytestmark = pytest.mark.unit


def test_ut_dln_01_fresh_deadline_has_budget():
    d = Deadline(1000)
    assert not d.expired
    assert 0 < d.remaining_ms() <= 1000
    assert d.can("retrieving", 10)
    assert not d.degraded


def test_ut_dln_02_expired_deadline_marks_degraded():
    d = Deadline(1)
    time.sleep(0.01)
    assert d.expired
    assert d.remaining_ms() == 0
    assert not d.can("synthesizing", 1)
    assert d.degraded
    assert d.expired_stage == "synthesizing"


def test_ut_dln_03_stage_does_not_raise_and_tracks():
    d = Deadline(5000)
    with d.stage("retrieving") as current:
        assert current is d
    assert not d.degraded
    assert "retrieving" in d.timings
    assert d.elapsed_ms() >= d.timings["retrieving"]


def test_ut_dln_04_stage_marks_degraded_after_overflow():
    d = Deadline(5)
    with d.stage("synthesis"):
        time.sleep(0.02)
    assert d.degraded
    assert d.expired_stage == "synthesis"


def test_ut_dln_05_zero_budget_is_expired_immediately():
    d = Deadline(0)
    assert d.expired
    assert not d.can("routing", 0)
    assert d.degraded
