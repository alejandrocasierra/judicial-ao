"""Diarización de hablantes en audio.

Intenta usar pyannote.audio si hay token de HuggingFace; de lo contrario
respalda a segmentación por energía/VAD (menos precisa pero no requiere token).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import soundfile as sf
import torch
import torchaudio

from app.providers.asr import extract_audio_to_wav
from app.core.config import get_settings

log = logging.getLogger(__name__)


def _load_audio(wav_path: Path, sample_rate: int = 16000) -> torch.Tensor:
    # soundfile evita incompatibilidades entre torchaudio 2.14 y soundfile 0.13
    # (torchaudio.load pasa argumentos que soundfile no acepta).
    array, sr = sf.read(str(wav_path), dtype="float32")
    waveform = torch.from_numpy(array)
    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)
    if sr != sample_rate:
        waveform = torchaudio.functional.resample(waveform, sr, sample_rate)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    return waveform


def _energy_based_diarization(wav_path: Path, min_duration_ms: int = 500) -> list[dict[str, Any]]:
    """Fallback: segmenta por energía usando VAD simple de torchaudio."""
    sample_rate = 16000
    waveform = _load_audio(wav_path, sample_rate)
    # VAD de torchaudio (requiere sample_rate 8000, 16000 o 32000)
    try:
        vad_transform = torchaudio.transforms.Vad(sample_rate=sample_rate)
        speech_waveform = vad_transform(waveform)
    except Exception as exc:
        log.warning("VAD de torchaudio no disponible: %s", exc)
        speech_waveform = waveform

    total_seconds = speech_waveform.shape[-1] / sample_rate
    # Divide en ventanas de 2 segundos como speakers distintos (muy conservador)
    window = 2.0
    segments = []
    start = 0.0
    while start < total_seconds:
        end = min(start + window, total_seconds)
        if (end - start) * 1000 >= min_duration_ms:
            segments.append({
                "start_ms": int(start * 1000),
                "end_ms": int(end * 1000),
                "label": f"SPK-{len(segments):02d}",
            })
        start = end
    return segments


def diarize(media_bytes: bytes, mime_type: str) -> list[dict[str, Any]]:
    """Devuelve segmentos de hablante con start_ms, end_ms y label."""
    s = get_settings()
    wav_path = extract_audio_to_wav(media_bytes, mime_type)
    try:
        token = s.ASR_DIARIZATION_TOKEN or s.HF_TOKEN
        if token:
            return _pyannote_diarization(wav_path, token)
        log.info("No hay HF_TOKEN; usando diarización por energía (fallback)")
        return _energy_based_diarization(wav_path)
    finally:
        wav_path.unlink(missing_ok=True)


def _pyannote_diarization(wav_path: Path, token: str) -> list[dict[str, Any]]:
    # Asegura que torchcodec encuentre FFmpeg en Windows si FFMPEG_PATH está configurado.
    s = get_settings()
    if s.FFMPEG_PATH:
        ffmpeg_dir = str(Path(s.FFMPEG_PATH).parent)
        if ffmpeg_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    from pyannote.audio import Pipeline
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=token)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipeline.to(torch.device(device))
    output = pipeline(str(wav_path))
    # pyannote.audio 4 devuelve DiarizeOutput con exclusive_speaker_diarization
    annotation = getattr(output, "exclusive_speaker_diarization", output)
    segments = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append({
            "start_ms": int(turn.start * 1000),
            "end_ms": int(turn.end * 1000),
            "label": speaker,
        })
    return segments
