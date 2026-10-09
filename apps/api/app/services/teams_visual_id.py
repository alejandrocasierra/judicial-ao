"""Identificación visual de hablantes en grabaciones de Microsoft Teams.

En Teams el participante que habla tiene su nombre resaltado con un fondo de color
(azul/teal/violeta) en la esquina de su mosaico; los que escuchan lo tienen sobre
fondo gris oscuro. Este módulo:

1. Extrae frames del video cada N segundos (UNA sola pasada de FFmpeg).
2. Localiza el resaltado por COLOR (máscara azul-violeta, ~0.02 s/frame) => "firma"
   del hablante activo (posición/tamaño de su etiqueta de nombre).
3. Hace OCR *sólo* cuando la firma cambia respecto a las cajas ya vistas: el OCR en
   CPU es lo caro (~1 s), así que se evita repetirlo mientras habla la misma persona.
   El coste pasa de O(n_frames) (~1 600 OCR en un video de 2 h) a O(n_cambios).
4. Devuelve una línea de tiempo [(timestamp_ms, nombre)] que el pipeline cruza con
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

# --- Detección por color del nombre resaltado (pixel a pixel) -----------------
# Saturación (max-min)/max mínima del fondo del nombre para considerarlo resaltado.
_MIN_HIGHLIGHT_SATURATION = 0.25
# CROMA mínima = max(B, R) - G. Alto en resaltados AZULES/teal (B>G) y PÚRPURAS/magenta
# (R y B > G); ~0 en fondos gris/negro (etiquetas inactivas: el que no habla).
_MIN_HIGHLIGHT_CHROMA = 15.0

# --- Firma del hablante activo (barata, para evitar OCR repetido) --------------
# Se calcula sobre el frame reducido (más rápido) y busca el rectángulo AZUL-VIOLETA
# típico de la etiqueta resaltada: B claramente por encima de R y G.
_SIG_SCALE = 0.5
_SIG_MIN_BLUE_DOM = 22.0
_SIG_MIN_AREA = 80
# Tolerancia (px) para considerar que dos firmas son "el mismo hablante".
_SIG_MATCH_PX = 44.0
_MAX_CACHE = 300

_DEFAULT_STEP_S = 5.0

_engine = None
_cv2 = None


def _get_cv2():
    global _cv2
    if _cv2 is None:
        import cv2  # noqa: PLC0415 — import diferido (imagen pesada)
        _cv2 = cv2
    return _cv2


def _get_engine():
    """RapidOCR afinado: sin clasificador de orientación y con detección a 960 px
    de lado máximo. Ambos ajustes recortan ~2x el tiempo de OCR (los nombres de
    Teams son horizontales, así que el clasificador no aporta)."""
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        try:
            _engine = RapidOCR(params={"Global.use_cls": False, "Global.max_side_len": 960})
        except Exception:  # noqa: BLE001 — versión/params distintos: usar defaults
            log.warning("No se pudieron aplicar los params de RapidOCR; usando defaults", exc_info=True)
            _engine = RapidOCR()
    return _engine


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
    """Devuelve (saturación, croma) del fondo detrás de la caja de texto.

    `croma = max(B, R) - G`: alto en resaltados azules/teal (B>G) y púrpuras/magenta
    (R y B > G); ~0 en fondos gris/negro (etiquetas inactivas: el que no habla)."""
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
    chroma = max(float(mean[2]), float(mean[0])) - float(mean[1])  # max(B,R) - G (RGB)
    return saturation, chroma


def _is_highlighted(image_arr: np.ndarray, box: Any) -> bool:
    saturation, chroma = _box_background(image_arr, box)
    return saturation >= _MIN_HIGHLIGHT_SATURATION and chroma >= _MIN_HIGHLIGHT_CHROMA


def _active_speaker_box(rgb: np.ndarray) -> tuple[int, int, int, int] | None:
    """Localiza por color la etiqueta RESALTADA (hablante activo). Rápido (~20 ms).

    Devuelve (x, y, w, h) en píxeles del frame original, o None si no hay resaltado
    (p. ej. compartición de pantalla o vídeo sin rejilla de participantes)."""
    cv2 = _get_cv2()
    h, w = rgb.shape[:2]
    small = cv2.resize(rgb, (max(1, int(w * _SIG_SCALE)), max(1, int(h * _SIG_SCALE))),
                       interpolation=cv2.INTER_AREA)
    f = small.astype(np.float32)
    b, g, r = f[..., 2], f[..., 1], f[..., 0]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = (mx - mn) / (mx + 1e-6)
    mask = ((b - np.maximum(r, g) >= _SIG_MIN_BLUE_DOM) & (sat >= 0.15)).astype(np.uint8)
    n, _labels, stats, _cents = cv2.connectedComponentsWithStats(mask, 8)
    inv = 1.0 / _SIG_SCALE
    best: tuple[int, int, int, int] | None = None
    best_score = 0.0
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if area < _SIG_MIN_AREA:
            continue
        fill = area / (ww * hh)
        ar = ww / max(hh, 1)
        aw, ah = ww * inv, hh * inv
        if ah < 12 or ah > 80 or aw < 50 or aw > 1000:
            continue
        if ar < 2.0 or ar > 26 or fill < 0.45:
            continue
        score = area * fill
        if score > best_score:
            best_score = score
            best = (int(x * inv), int(y * inv), int(aw), int(ah))
    return best


def _cache_lookup(cache: list[tuple[float, float, str | None]], cx: float, cy: float) -> tuple[str | None, bool]:
    """Busca si el resaltado (cx, cy) ya se leyó; tolerante a jitter de layout."""
    for ex, ey, name in cache:
        if abs(ex - cx) <= _SIG_MATCH_PX and abs(ey - cy) <= _SIG_MATCH_PX:
            return name, True
    return None, False


def _pick_name(out: Any, rgb: np.ndarray, sig_box: tuple[int, int, int, int] | None) -> str | None:
    """Elige el nombre del hablante activo en el resultado OCR del frame.

    1) Prioriza el texto cuya caja cae dentro de la etiqueta resaltada (muy fiable).
    2) Si no, aplica el test de color sobre el fondo de cada caja de texto."""
    boxes = getattr(out, "boxes", None)
    txts = getattr(out, "txts", None) or ()
    if boxes is None:
        return None

    if sig_box is not None:
        x, y, w, h = sig_box
        for i, t in enumerate(txts):
            if i >= len(boxes):
                continue
            t = (t or "").strip()
            if not _looks_like_name(t):
                continue
            pts = np.asarray(boxes[i], dtype=np.float32)
            cx = float(pts[:, 0].mean())
            cy = float(pts[:, 1].mean())
            if x - 12 <= cx <= x + w + 12 and y - 10 <= cy <= y + h + 10:
                return t

    best: str | None = None
    best_chroma = 0.0
    for i, t in enumerate(txts):
        if i >= len(boxes):
            continue
        t = (t or "").strip()
        if not _looks_like_name(t):
            continue
        if not _is_highlighted(rgb, boxes[i]):
            continue
        _, chroma = _box_background(rgb, boxes[i])
        if chroma > best_chroma:
            best_chroma = chroma
            best = t
    return best


def detect_active_speaker_name(image: Image.Image) -> str | None:
    """Devuelve el nombre RESALTADO (hablante activo) del frame, o None."""
    engine = _get_engine()
    rgb = np.asarray(image.convert("RGB"))
    out = engine(rgb[:, :, ::-1])  # RapidOCR espera BGR
    if not out or not getattr(out, "txts", None):
        return None
    return _pick_name(out, rgb, _active_speaker_box(rgb))


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
        # Cache: (cx, cy, nombre) de cada etiqueta resaltada ya leída. Si un frame
        # resalta la MISMA caja, reutilizamos el nombre SIN volver a hacer OCR.
        cache: list[tuple[float, float, str | None]] = []
        with tempfile.TemporaryDirectory(prefix="frames-") as td:
            ffmpeg = _get_ffmpeg_exe()
            cmd = [ffmpeg, "-y", "-i", str(src_path), "-vf", f"fps=1/{step_s}",
                   "-q:v", "3", str(Path(td) / "f_%06d.jpg")]
            subprocess.run(cmd, check=True, capture_output=True)
            frames = sorted(Path(td).glob("f_*.jpg"))
            total = len(frames) or 1
            engine = _get_engine()
            ocr_calls = 0
            log.info("Identificación visual: %d frames extraídos (1 pasada ffmpeg, cada %ss)", len(frames), step_s)
            for i, frame_path in enumerate(frames):
                ts = i * step_s
                try:
                    rgb = np.asarray(Image.open(frame_path).convert("RGB"))
                    name: str | None = None
                    box = _active_speaker_box(rgb)
                    if box is not None:
                        cx = box[0] + box[2] / 2.0
                        cy = box[1] + box[3] / 2.0
                        name, hit = _cache_lookup(cache, cx, cy)
                        if not hit:
                            out = engine(rgb[:, :, ::-1])  # RapidOCR espera BGR
                            ocr_calls += 1
                            name = _pick_name(out, rgb, box)
                            cache.append((cx, cy, name))
                            if len(cache) > _MAX_CACHE:
                                cache.pop(0)
                    if name:
                        results.append({"timestamp_ms": int(ts * 1000), "name": name})
                except Exception as exc:  # noqa: BLE001 — un frame no debe tumbar el pipeline
                    log.warning("No se pudo procesar frame %s: %s", frame_path.name, exc)
                if progress_cb:
                    try:
                        progress_cb(i + 1, total)
                    except Exception:  # noqa: BLE001 — el callback no debe tumbar el pipeline
                        pass
            log.info("Identificación visual: %d OCR realizados de %d frames (%d muestras de hablante)",
                     ocr_calls, len(frames), len(results))
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
