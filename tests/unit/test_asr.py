"""UT-ASR — proveedores ASR (Fase 3)."""
from __future__ import annotations

import pytest

from app.providers.asr import FakeASR, WhisperASR, extract_audio_to_wav, get_asr_provider

pytestmark = pytest.mark.unit


def test_ut_asr_01_fake_asr_returns_segments():
    provider = FakeASR()
    segments = provider.transcribe(b"fake media bytes", "video/mp4")
    assert len(segments) == 2
    assert segments[0]["start_ms"] == 0
    assert segments[0]["end_ms"] == 5000
    assert segments[0]["text"]
    assert 0 <= segments[0]["confidence"] <= 1


def test_ut_asr_02_get_provider_is_fake_in_tests():
    provider = get_asr_provider()
    assert provider.name == "fake"


def test_ut_asr_03_whisper_provider_class_exists():
    assert WhisperASR.name == "whisper"


def test_ut_asr_04_extract_audio_to_wav_rejects_empty_bytes():
    with pytest.raises(RuntimeError):
        extract_audio_to_wav(b"", "video/mp4")
