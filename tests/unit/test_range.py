"""UT-RNG — parseo de HTTP Range para el streaming de media."""
from __future__ import annotations

import pytest

from app.routers.documents import _parse_range

pytestmark = pytest.mark.unit


def test_ut_rng_01_closed_range():
    assert _parse_range("bytes=0-2047", 5000) == (0, 2047)
    assert _parse_range("bytes=1000-1999", 155814192) == (1000, 1999)


def test_ut_rng_02_open_ended_range():
    # El navegador suele pedir "bytes=0-": el endpoint lo limita luego a un trozo.
    assert _parse_range("bytes=0-", 5000) == (0, 4999)


def test_ut_rng_03_suffix_range():
    assert _parse_range("bytes=-1000", 5000) == (4000, 4999)


def test_ut_rng_04_clamps_end_to_total():
    assert _parse_range("bytes=4500-9999", 5000) == (4500, 4999)


def test_ut_rng_05_invalid_is_unsatisfiable():
    assert _parse_range("bytes=9999-10000", 5000) == (None, None)
    assert _parse_range("basura", 5000) == (None, None)
