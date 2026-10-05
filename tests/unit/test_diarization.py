"""UT-DIAR — diarización de hablantes (Fase 3)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.diarization import _energy_based_diarization, diarize

pytestmark = pytest.mark.unit


def _write_noise_wav(path, duration_s: int = 5, sample_rate: int = 16000) -> None:
    import wave
    import struct
    import random
    random.seed(42)
    n_frames = duration_s * sample_rate
    samples = [int(random.uniform(-3000, 3000)) for _ in range(n_frames)]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<" + "h" * n_frames, *samples))


def test_ut_diar_01_energy_fallback_creates_segments(tmp_path):
    wav_path = tmp_path / "noise.wav"
    _write_noise_wav(wav_path, duration_s=5)

    with patch("app.services.diarization._load_audio") as mock_load:
        with patch("torchaudio.transforms.Vad") as mock_vad:
            import torch
            mock_load.return_value = torch.zeros(1, 16000 * 5)
            mock_vad.return_value.side_effect = lambda x: x
            segments = _energy_based_diarization(wav_path, min_duration_ms=500)

    assert len(segments) > 0
    assert segments[0]["start_ms"] == 0
    assert segments[-1]["end_ms"] <= 5000
    for seg in segments:
        assert "start_ms" in seg
        assert "end_ms" in seg
        assert seg["label"].startswith("SPK-")


def test_ut_diar_02_diarize_uses_pyannote_when_token_set(tmp_path):
    wav_path = tmp_path / "fake.wav"
    _write_noise_wav(wav_path, duration_s=5)

    fake_output = MagicMock()
    fake_annotation = MagicMock()
    fake_annotation.itertracks.return_value = [
        (MagicMock(start=0.0, end=1.5), None, "SPEAKER_00"),
        (MagicMock(start=2.0, end=3.5), None, "SPEAKER_01"),
    ]
    fake_output.exclusive_speaker_diarization = fake_annotation

    with patch("app.services.diarization.get_settings") as mock_settings:
        mock_settings.return_value.ASR_DIARIZATION_TOKEN = "hf_test"
        mock_settings.return_value.HF_TOKEN = "hf_test"
        mock_settings.return_value.FFMPEG_PATH = ""
        with patch("app.services.diarization.extract_audio_to_wav", return_value=wav_path):
            with patch("pyannote.audio.Pipeline.from_pretrained") as mock_from_pretrained:
                pipeline = MagicMock()
                pipeline.return_value = fake_output
                mock_from_pretrained.return_value = pipeline
                segments = diarize(b"fake", "video/mp4")

    assert len(segments) == 2
    assert segments[0]["label"] == "SPEAKER_00"
    assert segments[1]["label"] == "SPEAKER_01"
