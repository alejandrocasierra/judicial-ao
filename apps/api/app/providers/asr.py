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
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import os
import shutil
from pathlib import Path
from typing import Any

import soundfile as sf

from app.core.config import get_settings

log = logging.getLogger(__name__)


def _fmt_ms(ms: int) -> str:
    """Formatea milisegundos como mm:ss (para logs en vivo)."""
    total_s = int(ms // 1000)
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


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


class _ChildDied(Exception):
    """El proceso hijo de Whisper murió a mitad de un tramo (p. ej. OOM/SIGKILL)."""


def _mem_limit_bytes() -> int:
    """Límite de memoria del contenedor (cgroup v2/v1). 0 = desconocido."""
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = Path(p).read_text().strip()
            if v and v != "max":
                return int(v)
        except Exception as exc:  # noqa: BLE001
            log.debug("no se pudo leer el límite de memoria en %s: %s", p, exc)
            continue
    return 0


class _WhisperChild:
    """Proceso hijo de Whisper (subprocess + JSON-lines), supervisado por el padre.

    Los workers Celery son procesos daemon y NO pueden tener hijos con
    `multiprocessing`; por eso se usa `subprocess.Popen`. Si el hijo muere (OOM),
    el padre lo detecta, lo recrea y reintenta el tramo — el job NUNCA muere."""

    STALL_SECONDS = 900  # sin mensajes del hijo en 15 min => colgado

    def __init__(self, model_kwargs: dict, transcribe_opts: dict) -> None:
        self._model_kwargs = model_kwargs
        self._opts = transcribe_opts
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue | None = None
        self._reader: threading.Thread | None = None

    @staticmethod
    def _reader_loop(stream: Any, q: "queue.Queue") -> None:
        for line in stream:
            try:
                q.put(json.loads(line))
            except Exception as exc:  # noqa: BLE001
                log.debug("línea JSON inválida del hijo ASR: %s", exc)
                continue
        q.put(None)  # EOF: el hijo cerró stdout (murió o salió)

    def _send(self, obj: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def start(self) -> None:
        self.close()
        self._q = queue.Queue()
        # cwd = apps/api para que `python -m app.providers.asr_child` sea importable.
        app_dir = Path(__file__).resolve().parents[2]
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "app.providers.asr_child"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            cwd=str(app_dir))
        self._reader = threading.Thread(
            target=self._reader_loop, args=(self._proc.stdout, self._q), daemon=True)
        self._reader.start()
        self._send({"type": "init", "model_kwargs": self._model_kwargs, "opts": self._opts})
        deadline = time.time() + 600  # la carga del modelo puede tardar
        while time.time() < deadline:
            try:
                msg = self._q.get(timeout=5)
            except queue.Empty:
                if self._proc.poll() is not None:
                    raise _ChildDied(f"el hijo murió cargando el modelo (exit {self._proc.returncode})") from None
                continue
            if msg is None:
                raise _ChildDied("el hijo cerró stdout cargando el modelo")
            if msg.get("type") == "ready":
                return
        raise RuntimeError("el hijo de Whisper no cargó el modelo en 10 min")

    def transcribe_chunk(self, wav_path: Path, start_frame: int, frames: int,
                         on_segment: Any) -> None:
        """Envía un tramo al hijo; on_segment(seg_dict, frac) por cada segmento."""
        self._send({"type": "chunk", "wav": str(wav_path),
                    "start": int(start_frame), "frames": int(frames)})
        silent = 0
        while True:
            try:
                msg = self._q.get(timeout=5)
            except queue.Empty:
                silent += 5
                if self._proc is not None and self._proc.poll() is not None:
                    raise _ChildDied(f"el hijo salió con código {self._proc.returncode} (OOM/SIGKILL)") from None
                if silent > self.STALL_SECONDS:
                    raise _ChildDied("el hijo dejó de responder (stall)") from None
                continue
            if msg is None:
                raise _ChildDied("EOF: el hijo cerró stdout (murió)")
            silent = 0
            tag = msg.get("type")
            if tag == "seg":
                on_segment(msg["seg"], msg.get("frac", 0.0))
            elif tag == "ok":
                return
            elif tag == "err":
                raise RuntimeError(f"Whisper hijo: {msg.get('msg')}")

    def close(self) -> None:
        try:
            if self._proc is not None and self._proc.poll() is None:
                self._send({"type": "stop"})
        except Exception as exc:  # noqa: BLE001
            log.debug("no se pudo enviar stop al hijo ASR: %s", exc)
        if self._proc is not None:
            try:
                self._proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                self._proc.kill()
        self._proc = None

    def rss_mb(self) -> int:
        """RSS del hijo en MB (Linux /proc). 0 si no se puede leer."""
        try:
            if self._proc is None:
                return 0
            with open(f"/proc/{self._proc.pid}/status", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) // 1024
        except Exception:  # noqa: BLE001
            return 0
        return 0


class WhisperASR:
    """ASR local con faster-whisper EN PROCESO HIJO SUPERVISADO (a prueba de OOM)."""

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

    def _download_root(self) -> str:
        cache = os.environ.get("WHISPER_CACHE_DIR")
        if cache:
            Path(cache).mkdir(parents=True, exist_ok=True)
            return cache
        fallback = str(Path.home() / ".cache" / "whisper")
        Path(fallback).mkdir(parents=True, exist_ok=True)
        return fallback

    def _adaptive_chunk_s(self) -> int:
        """Tramo base de config ajustado a la MEMORIA del contenedor: menos RAM →
        tramos más cortos (menor pico). La VPS (worker-media ~3g) y la máquina local
        (6.5g) usan cada una el tramo más grande que soportan sin OOM."""
        base = int(getattr(get_settings(), "ASR_CHUNK_SECONDS", 300) or 300)
        limit_gb = _mem_limit_bytes() / 1e9
        if limit_gb:
            if limit_gb < 3.5:    # VPS e2-medium (worker-media 3000m)
                base = min(base, 120)
            elif limit_gb < 5.5:
                base = min(base, 180)
            elif limit_gb < 7.5:  # local (worker-media 6500m)
                base = min(base, 300)
        return max(60, base)

    def transcribe(self, media: bytes | Path, mime_type: str,
                   progress_cb: Any | None = None,
                   initial_prompt: str | None = None) -> list[dict[str, Any]]:
        if not media:
            raise ValueError("media is empty")

        wav_path = extract_audio_to_wav(media, mime_type)
        try:
            info = sf.info(str(wav_path))
            sr = int(info.samplerate or 16000)
            total_s = info.frames / sr
            chunk_s = self._adaptive_chunk_s()
            log.info("ASR: %.0f s de audio en tramos de %d s (límite de memoria: %.1f GB)",
                     total_s, chunk_s, _mem_limit_bytes() / 1e9)

            model_kwargs = dict(
                model_size_or_path=self.model_size, device=self.device,
                compute_type=self.compute_type, download_root=self._download_root(),
                cpu_threads=self.cpu_threads, num_workers=1)
            opts = dict(
                language="es", beam_size=self.beam_size, best_of=self.best_of,
                vad_filter=self.vad_filter, vad_parameters=self.vad_parameters or None,
                word_timestamps=True, condition_on_previous_text=True,
                initial_prompt=initial_prompt or None)
            log_live = bool(getattr(get_settings(), "ASR_LOG_LIVE", False))
            recycle_s = int(getattr(get_settings(), "ASR_CHILD_RECYCLE_SECONDS", 900) or 0)
            child_done_s = 0.0

            child = _WhisperChild(model_kwargs, opts)
            child.start()
            try:
                # Cola de tramos (start_s, dur_s). Si un tramo MATA al hijo, se parte a
                # la mitad y se reintenta: el job nunca muere ni se salta de video.
                queue: list[tuple[float, float]] = [
                    (i * chunk_s, min(chunk_s, total_s - i * chunk_s))
                    for i in range(math.ceil(total_s / chunk_s))
                ]
                done_s = 0.0
                out: list[dict[str, Any]] = []
                failed: list[tuple[float, float]] = []
                while queue:
                    start_s, dur_s = queue.pop(0)
                    offset_ms = int(round(start_s * 1000))
                    segs: list[dict[str, Any]] = []

                    def on_segment(d: dict, frac: float, _off=offset_ms, _ds=dur_s,
                                   _segs=segs, _done=done_s) -> None:
                        if _off:
                            d["start_ms"] += _off
                            d["end_ms"] += _off
                            for w in d.get("words", []):
                                w["start_ms"] += _off
                                w["end_ms"] += _off
                        _segs.append(d)
                        if log_live:
                            log.info("ASR [%s–%s] (conf %.2f) %s", _fmt_ms(d["start_ms"]),
                                     _fmt_ms(d["end_ms"]), d["confidence"], d["text"])
                        if progress_cb and total_s:
                            try:
                                progress_cb(min(0.999, (_done + frac * _ds) / total_s))
                            except Exception as exc:  # noqa: BLE001
                                log.debug("progress_cb del ASR falló: %s", exc)

                    try:
                        child.transcribe_chunk(wav_path, int(start_s * sr), int(dur_s * sr),
                                               on_segment)
                    except _ChildDied as exc:
                        log.warning("ASR: el hijo murió en el tramo %.0f–%.0f s: %s. "
                                    "Se recrea y se reintenta.", start_s, start_s + dur_s, exc)
                        child.start()  # recrea (recarga el modelo desde caché)
                        if dur_s > 120:  # partir a la mitad y reintentar
                            half = dur_s / 2
                            queue.insert(0, (start_s + half, dur_s - half))
                            queue.insert(0, (start_s, half))
                            log.info("ASR: tramo de %.0f s partido en 2 de %.0f s", dur_s, half)
                            continue
                        failed.append((start_s, dur_s))
                        log.error("ASR: tramo %.0f–%.0f s falló incluso al mínimo; queda marcado",
                                  start_s, start_s + dur_s)
                        continue
                    done_s += dur_s
                    child_done_s += dur_s
                    out.extend(segs)
                    log.info("ASR: tramo %.0f–%.0f s listo (%d segmentos; %.0f%% del audio; hijo RSS %d MB)",
                             start_s, start_s + dur_s, len(segs), 100.0 * done_s / max(1.0, total_s),
                             child.rss_mb())
                    # Reciclaje del hijo: la memoria de faster-whisper/ctranslate2 crece
                    # en procesos muy largos. Al recrear el proceso, el RSS vuelve al
                    # baseline y el siguiente hijo CONTINÚA donde quedó este (secuencial,
                    # nunca dos hijos a la vez → el pico de RAM nunca se duplica).
                    if recycle_s and child_done_s >= recycle_s and queue:
                        log.info("ASR: reciclando el hijo tras %.0f s de audio (vuelve al baseline)",
                                 child_done_s)
                        child.start()
                        child_done_s = 0.0
                        log.info("ASR: hijo nuevo listo (RSS %d MB)", child.rss_mb())
                if failed:
                    raise RuntimeError(
                        f"ASR: {len(failed)} tramo(s) no transcribibles incluso al mínimo: {failed}")
                if progress_cb:
                    try:
                        progress_cb(1.0)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("progress_cb final del ASR falló: %s", exc)
                return out
            finally:
                child.close()
        finally:
            wav_path.unlink(missing_ok=True)


class FakeASR:
    """ASR dummy para tests/desarrollo."""

    name = "fake"

    def transcribe(self, media_bytes: bytes, mime_type: str,
                   progress_cb: Any | None = None,
                   initial_prompt: str | None = None) -> list[dict[str, Any]]:
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
