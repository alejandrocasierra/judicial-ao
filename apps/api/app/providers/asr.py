"""Proveedores ASR (Automatic Speech Recognition) para audio/video local.

Implementaciones:
- WhisperASR: faster-whisper + FFmpeg local. Soporta CPU/CUDA y fallback a CPU.
- FakeASR: para tests/desarrollo sin modelo.

La salida es una lista de segmentos con:
- start_ms / end_ms
- text
- confidence (media de palabras)
- words: lista de {word, start_ms, end_ms, confidence}
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import soundfile as sf
from faster_whisper import WhisperModel

from app.core.config import get_settings

log = logging.getLogger(__name__)


def _get_ffmpeg_exe() -> str:
    """Resuelve el ejecutable FFmpeg: path explícito, PATH, o imageio-ffmpeg."""
    s = get_settings()
    if s.FFMPEG_PATH and Path(s.FFMPEG_PATH).exists():
        return str(s.FFMPEG_PATH)
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        from imageio_ffmpeg import get_ffmpeg_exe as imageio_ffmpeg
        return imageio_ffmpeg()
    except Exception as exc:
        raise RuntimeError("FFmpeg no encontrado. Instala FFmpeg o configura FFMPEG_PATH") from exc


def extract_audio_to_wav(media: bytes | Path, mime_type: str, sample_rate: int = 16000) -> Path:
    """Extrae pista de audio 16 kHz mono a un WAV temporal.

    Acepta los bytes del archivo **o una ruta** a un archivo ya descargado (evita cargar
    archivos grandes en memoria)."""
    cleanup_src: Path | None = None
    if isinstance(media, (bytes, bytearray)):
        ext = "mp4" if "video" in mime_type else "wav"
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as src:
            src.write(media)
            src_path = Path(src.name)
        cleanup_src = src_path
    else:
        src_path = Path(media)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst:
        dst_path = Path(dst.name)

    ffmpeg = _get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-i", str(src_path),
        "-vn", "-acodec", "pcm_s16le",
        "-ar", str(sample_rate), "-ac", "1",
        str(dst_path),
    ]
    log.info("Extrayendo audio con FFmpeg: %s", dst_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if cleanup_src:
        cleanup_src.unlink(missing_ok=True)
    if result.returncode != 0:
        dst_path.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg falló: {result.stderr}")
    return dst_path


def _segment_to_dict(segment) -> dict[str, Any]:
    words = []
    for w in (segment.words or []):
        words.append({
            "word": w.word,
            "start_ms": int(w.start * 1000),
            "end_ms": int(w.end * 1000),
            "confidence": getattr(w, "probability", 0.0) or 0.0,
        })
    confs = [w["confidence"] for w in words if w["confidence"] > 0]
    confidence = sum(confs) / len(confs) if confs else (segment.avg_logprob or 0.0)
    return {
        "start_ms": int(segment.start * 1000),
        "end_ms": int(segment.end * 1000),
        "text": segment.text.strip(),
        "confidence": float(max(0.0, min(1.0, confidence))),
        "words": words,
    }


class WhisperASR:
    """ASR local con faster-whisper."""

    name = "whisper"

    def __init__(self) -> None:
        s = get_settings()
        self.model_size = s.ASR_MODEL or "large-v3"
        self.device = s.ASR_DEVICE or "cpu"
        self.compute_type = s.ASR_COMPUTE_TYPE or "int8"
        self.beam_size = s.ASR_BEAM_SIZE or 5
        self.best_of = s.ASR_BEST_OF or 5
        self.vad_filter = s.ASR_VAD_FILTER
        self.vad_parameters = getattr(s, "ASR_VAD_PARAMETERS", None) or {}
        # 0 = automático; en VPS pequeñas se fija (p. ej. 2) para no saturar la máquina.
        self.cpu_threads = int(getattr(s, "ASR_CPU_THREADS", 0) or 0)
        self._model: WhisperModel | None = None

    def _download_root(self) -> str:
        cache = os.environ.get("WHISPER_CACHE_DIR")
        if cache:
            Path(cache).mkdir(parents=True, exist_ok=True)
            return cache
        fallback = str(Path.home() / ".cache" / "whisper")
        Path(fallback).mkdir(parents=True, exist_ok=True)
        return fallback

    def _load_model(self) -> WhisperModel:
        if self._model is None:
            download_root = self._download_root()
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=download_root,
                    cpu_threads=self.cpu_threads,
                    num_workers=1,
                )
            except Exception as exc:
                log.warning("No se pudo cargar Whisper en %s/%s: %s. Fallback a CPU int8.",
                            self.device, self.compute_type, exc)
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                    download_root=download_root,
                    cpu_threads=self.cpu_threads,
                    num_workers=1,
                )
        return self._model

    def transcribe(self, media: bytes | Path, mime_type: str,
                   progress_cb: Any | None = None) -> list[dict[str, Any]]:
        if not media:
            raise ValueError("media is empty")

        wav_path = extract_audio_to_wav(media, mime_type)
        try:
            model = self._load_model()
            info = sf.info(str(wav_path))
            sr = int(info.samplerate or 16000)
            total_s = info.frames / sr
            chunk_s = int(getattr(get_settings(), "ASR_CHUNK_SECONDS", 600) or 0)

            # Audio corto: una sola pasada.
            if not chunk_s or total_s <= chunk_s:
                return self._transcribe_source(model, str(wav_path), 0, None, progress_cb)

            # Audio largo: se TROCEA. faster-whisper consume memoria proporcional a la
            # duración (~55 MB/min): 2 h en una sola pasada ~8 GB (OOM). Por trozos de
            # 10 min el pico queda acotado (~1 GB) en local y en la VPS.
            out: list[dict[str, Any]] = []
            n_chunks = math.ceil(total_s / chunk_s)
            for i in range(n_chunks):
                start_frame = i * chunk_s * sr
                frames = int(min(chunk_s * sr, info.frames - start_frame))
                if frames <= 0:
                    break
                data, _ = sf.read(str(wav_path), start=start_frame, frames=frames,
                                  dtype="float32", always_2d=False)
                offset_ms = int(round(start_frame / sr * 1000))
                out.extend(self._transcribe_source(model, data, offset_ms, (i, n_chunks), progress_cb))
            return out
        finally:
            wav_path.unlink(missing_ok=True)

    def _transcribe_source(self, model: WhisperModel, source: Any, offset_ms: int,
                           chunk_info: tuple[int, int] | None,
                           progress_cb: Any | None) -> list[dict[str, Any]]:
        segments, info = model.transcribe(
            source,
            language="es",
            beam_size=self.beam_size,
            best_of=self.best_of,
            vad_filter=self.vad_filter,
            vad_parameters=self.vad_parameters or None,
            word_timestamps=True,
            condition_on_previous_text=True,
        )
        total = float(getattr(info, "duration", 0) or 0)
        out: list[dict[str, Any]] = []
        for seg in segments:
            d = _segment_to_dict(seg)
            if offset_ms:
                d["start_ms"] += offset_ms
                d["end_ms"] += offset_ms
                for w in d.get("words", []):
                    w["start_ms"] += offset_ms
                    w["end_ms"] += offset_ms
            out.append(d)
            if progress_cb and total:
                base = (chunk_info[0] / chunk_info[1]) if chunk_info else 0.0
                span = (1.0 / chunk_info[1]) if chunk_info else 1.0
                try:
                    progress_cb(min(0.999, base + span * min(1.0, float(seg.end) / total)))
                except Exception:  # noqa: BLE001 — el progreso no debe tumbar el ASR
                    pass
        if progress_cb and chunk_info is None:
            try:
                progress_cb(1.0)
            except Exception:  # noqa: BLE001
                pass
        return out


class FakeASR:
    """ASR dummy para tests/desarrollo."""

    name = "fake"

    def transcribe(self, media_bytes: bytes, mime_type: str,
                   progress_cb: Any | None = None) -> list[dict[str, Any]]:
        return [
            {
                "start_ms": 0,
                "end_ms": 5000,
                "text": "Este es un segmento de prueba del proveedor ASR fake.",
                "confidence": 0.95,
                "words": [],
            },
            {
                "start_ms": 5200,
                "end_ms": 9000,
                "text": "Segundo segmento de prueba para validar el pipeline.",
                "confidence": 0.92,
                "words": [],
            },
        ]


def get_asr_provider() -> WhisperASR | FakeASR:
    s = get_settings()
    if s.ASR_PROVIDER == "fake":
        return FakeASR()
    return WhisperASR()
