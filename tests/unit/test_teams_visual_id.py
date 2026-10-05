"""UT-TEAMS — identificación visual del HABLANTE ACTIVO en Teams (Fase 3)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from app.services.teams_visual_id import (
    _is_highlighted,
    _looks_like_name,
    detect_active_speaker_name,
    extract_active_speaker_timeline,
)

pytestmark = pytest.mark.unit


def test_ut_teams_01_looks_like_name_filters_noise():
    assert _looks_like_name("Jose Alfredo Molina Ibarra") is True
    assert _looks_like_name("CARLOS ALFONSO GOMEZ GARCES") is True
    assert _looks_like_name("110013103021201800") is False
    assert _looks_like_name("2018-13-61") is False
    assert _looks_like_name("A") is False
    assert _looks_like_name("jorge") is False          # una sola palabra
    assert _looks_like_name("") is False


def test_ut_teams_02_is_highlighted_detects_blue_background():
    box = np.array([[0, 0], [50, 0], [50, 20], [0, 20]], dtype=float)
    gray = np.zeros((100, 100, 3), dtype=np.uint8)
    assert _is_highlighted(gray, box) is False          # fondo gris oscuro
    blue = np.zeros((100, 100, 3), dtype=np.uint8)
    blue[:20, :50] = (115, 118, 174)                    # azul de Teams
    assert _is_highlighted(blue, box) is True


def test_ut_teams_03_detect_active_speaker_picks_highlighted_name():
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    img[1040:1075, 900:1240] = (115, 118, 174)          # franja azul del hablante activo

    fake_out = MagicMock()
    fake_out.txts = ["CARLOS ALFONSO GOMEZ GARCES", "Alba Lucy Cock Alvarez"]
    fake_out.boxes = [
        np.array([[100, 1045], [500, 1045], [500, 1070], [100, 1070]], dtype=float),   # sin resaltar
        np.array([[910, 1045], [1230, 1045], [1230, 1070], [910, 1070]], dtype=float),  # azul
    ]

    with patch("app.services.teams_visual_id._get_engine") as mock_engine:
        mock_engine.return_value.return_value = fake_out
        name = detect_active_speaker_name(Image.fromarray(img))

    assert name == "Alba Lucy Cock Alvarez"


def test_ut_teams_04_timeline_returns_active_names():
    with patch("app.services.teams_visual_id._get_duration", return_value=25.0):
        with patch("app.services.teams_visual_id._extract_frame"):
            with patch("PIL.Image.open") as mock_open:
                mock_open.return_value = Image.new("RGB", (1920, 1080), color="black")
                with patch("app.services.teams_visual_id.detect_active_speaker_name",
                           return_value="Alba Lucy Cock Alvarez"):
                    results = extract_active_speaker_timeline(b"fake", "video/mp4", step_s=10.0)

    assert len(results) == 3  # 0, 10, 20 s
    assert all(r["name"] == "Alba Lucy Cock Alvarez" for r in results)
    assert [r["timestamp_ms"] for r in results] == [0, 10000, 20000]
