"""Identificación visual de hablantes en grabaciones de Microsoft Teams.

En Teams el participante que habla tiene su nombre resaltado con un fondo de color
(azul/teal) en la esquina inferior de su mosaico; los que escuchan lo tienen sobre
fondo gris oscuro. Este módulo:

1. Muestrea frames del video cada N segundos.
2. Detecta el nombre RESALTADO (fondo saturado azulado) = hablante activo.
3. Devuelve una línea de tiempo [(timestamp_ms, nombre)] que el pipeline cruza con
   la diarización de audio para poner nombre a cada SPEAKER_xx.
"""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.providers.asr import _get_ffmpeg_exe

log = logging.getLogger(__name__)

# Umbral de saturación (max-min)/max del fondo del nombre para considerarlo resaltado.
_MIN_HIGHLIGHT_SATURATION = 0.25
# Diferencia mínima azul-rojo para descartar resaltados rojizos/verdes (Teams usa azul).
_MIN_BLUE_DOMINANCE = 15.0
# Intervalo de muestreo de frames (segundos). Suficiente para no perder turnos.
_DEFAULT_STEP_S = 5.0

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR()
    return _engine


def _extract_frame(video_path: Path, timestamp_s: float, output_path: Path) -> None:
    ffmpeg = _get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-ss", str(timestamp_s),
        "-i", str(video_path), "-frames:v", "1",
        "-q:v", "3", str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _looks_like_name(text: str) -> bool:
    """Filtra texto que no sea un nombre de persona (números de caso, fechas, UI)."""
    if not text:
        return False
    words = text.split()
    if len(words) < 2:  # al menos nombre + apellido (descarta "jorge", "E.", "S.")
        return False
    alpha_words = [w for w in words if re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñÜü]", w)]
    if len(alpha_words) < 2:
        return False
    digits = sum(1 for ch in text if ch.isdigit())
    if digits / len(text) > 0.4:
        return False
    # Descarta etiquetas de UI frecuentes.
    if text.strip().lower() in {"nombre(s)", "apellido", "reunion", "participantes"}:
        return False
    return True


def _box_background(image_arr: np.ndarray, box: Any) -> tuple[float, float]:
    """Devuelve (saturación, dominancia_azul) del fondo detrás de la caja de texto."""
    pts = np.array(box)
    xs, ys = pts[:, 0], pts[:, 1]
    h, w = image_arr.shape[:2]
    x0, x1 = max(0, int(xs.min())), min(w, int(xs.max()))
    y0, y1 = max(0, int(ys.min())), min(h, int(ys.max()))
    crop = image_arr[y0:y1, x0:x1].astype(np.float32)
    if crop.size == 0:
        return 0.0, 0.0
    mean = crop.reshape(-1, 3).mean(axis=0)
    mx, mn = float(mean.max()), float(mean.min())
    saturation = (mx - mn) / (mx + 1e-6)
    blue_dominance = float(mean[2]) - float(mean[0])  # B - R
    return saturation, blue_dominance


def _is_highlighted(image_arr: np.ndarray, box: Any) -> bool:
    saturation, blue_dominance = _box_background(image_arr, box)
    return saturation >= _MIN_HIGHLIGHT_SATURATION and blue_dominance >= _MIN_BLUE_DOMINANCE


def detect_active_speaker_name(image: Image.Image) -> str | None:
    """Devuelve el nombre RESALTADO (hablante activo) del frame, o None."""
    engine = _get_engine()
    arr = np.array(image.convert("RGB"))
    out = engine(arr)
    if not out or not out.txts:
        return None
    boxes = getattr(out, "boxes", None)
    if boxes is None:
        return None

    best_name: str | None = None
    best_blue = 0.0
    for i, text in enumerate(out.txts):
        if i >= len(boxes):
            continue
        text = text.strip()
        if not _looks_like_name(text):
            continue
        if not _is_highlighted(arr, boxes[i]):
            continue
        _, blue = _box_background(arr, boxes[i])
        if blue > best_blue:
            best_blue = blue
            best_name = text
    return best_name


def extract_active_speaker_timeline(
    media: bytes | Path,
    mime_type: str,
    step_s: float = _DEFAULT_STEP_S,
    progress_cb: Any | None = None,
) -> list[dict[str, Any]]:
    """Línea de tiempo [{timestamp_ms, name}] del nombre resaltado en cada frame.

    Acepta los bytes del video **o una ruta** a un archivo ya descargado."""
    cleanup_src: Path | None = None
    if isinstance(media, (bytes, bytearray)):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as src:
            src.write(media)
            src_path = Path(src.name)
        cleanup_src = src_path
    else:
        src_path = Path(media)

    try:
        results: list[dict[str, Any]] = []
        # UNA SOLA pasada de FFmpeg: extrae 1 frame cada `step_s` segundos (fps=1/step_s).
        # Antes se hacía una llamada a FFmpeg POR frame (~1 600 en un video de 2 h) => lentísimo.
        with tempfile.TemporaryDirectory(prefix="frames-") as td:
            ffmpeg = _get_ffmpeg_exe()
            cmd = [ffmpeg, "-y", "-i", str(src_path), "-vf", f"fps=1/{step_s}",
                   "-q:v", "3", str(Path(td) / "f_%06d.jpg")]
            subprocess.run(cmd, check=True, capture_output=True)
            frames = sorted(Path(td).glob("f_*.jpg"))
            total = len(frames) or 1
            log.info("Identificación visual: %d frames extraídos (1 pasada ffmpeg, cada %ss)", len(frames), step_s)
            for i, frame_path in enumerate(frames):
                ts = i * step_s
                try:
                    image = Image.open(frame_path)
                    name = detect_active_speaker_name(image)
                    if name:
                        results.append({"timestamp_ms": int(ts * 1000), "name": name})
                except Exception as exc:  # noqa: BLE001 — un frame no debe tumbar el pipeline
                    log.warning("No se pudo procesar frame %s: %s", frame_path.name, exc)
                if progress_cb:
                    try:
                        progress_cb(i + 1, total)
                    except Exception:  # noqa: BLE001 — el callback no debe tumbar el pipeline
                        pass
        return results
    finally:
        if cleanup_src:
            cleanup_src.unlink(missing_ok=True)


# Compatibilidad: la versión anterior devolvía todos los nombres de la franja
# inferior. Ahora devolvemos sólo el resaltado (hablante activo).
def extract_visual_speaker_names(
    media_bytes: bytes, mime_type: str, sample_timestamps_s: list[float] | None = None,
) -> list[dict[str, Any]]:
    return extract_active_speaker_timeline(media_bytes, mime_type)


def _get_duration(video_path: Path) -> float:
    ffmpeg = _get_ffmpeg_exe()
    result = subprocess.run([ffmpeg, "-i", str(video_path)], capture_output=True, text=True)
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return 0.0
