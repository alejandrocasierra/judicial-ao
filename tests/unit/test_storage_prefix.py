"""UT-STO — carpeta (prefijo) del bucket para Google Cloud Storage / S3."""
from __future__ import annotations

import pytest

from app.services.storage import normalize_prefix, prefixed

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("raw,expected", [
    ("", ""),
    ("judicial-ai", "judicial-ai/"),
    ("judicial-ai/prod", "judicial-ai/prod/"),
    ("/judicial-ai/prod/", "judicial-ai/prod/"),
])
def test_ut_sto_01_normalize_prefix(raw, expected):
    assert normalize_prefix(raw) == expected


@pytest.mark.parametrize("prefix,key,expected", [
    ("", "cases/abc/originals/deadbeef", "cases/abc/originals/deadbeef"),
    ("judicial-ai/prod", "cases/abc/originals/deadbeef", "judicial-ai/prod/cases/abc/originals/deadbeef"),
    ("judicial-ai/dev/", "cases/abc/media/x", "judicial-ai/dev/cases/abc/media/x"),
])
def test_ut_sto_02_prefixed(prefix, key, expected):
    assert prefixed(prefix, key) == expected
