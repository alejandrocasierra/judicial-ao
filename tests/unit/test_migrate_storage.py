"""UT-MIG — clave lógica de la migración de storage local -> bucket."""
from __future__ import annotations

from pathlib import Path

import pytest

from migrate_storage import logical_key_from_path

pytestmark = pytest.mark.unit


def test_ut_mig_01_logical_key_is_posix_relative(tmp_path):
    root = tmp_path / "storage"
    p = root / "cases" / "abc" / "originals" / "deadbeef"
    assert logical_key_from_path(root, p) == "cases/abc/originals/deadbeef"


def test_ut_mig_02_handles_files_and_media(tmp_path):
    root = tmp_path / "storage"
    assert logical_key_from_path(root, root / "cases" / "c1" / "files" / "x.xlsx") \
        == "cases/c1/files/x.xlsx"
    assert logical_key_from_path(root, root / "cases" / "c1" / "media" / "y.mp4") \
        == "cases/c1/media/y.mp4"
    assert logical_key_from_path(root, Path(root, "cases", "c1", "pages", "d1", "0001.png")) \
        == "cases/c1/pages/d1/0001.png"
