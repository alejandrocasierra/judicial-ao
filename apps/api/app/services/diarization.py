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


def _wav_duration_minutes(wav_path: Path) -> float:
    try:
        info = sf.info(str(wav_path))
        return info.frames / float(info.samplerate or 16000) / 60.0
    except Exception:  # noqa: BLE001
        return 0.0


def _load_pipeline(token: str):
    """Crea el pipeline de pyannote una sola vez (se reutiliza en todos los tramos)."""
    s = get_settings()
    if s.FFMPEG_PATH:
        ffmpeg_dir = str(Path(s.FFMPEG_PATH).parent)
        if ffmpeg_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
    from pyannote.audio import Pipeline
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=token)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipeline.to(torch.device(device))
    return pipeline


def _pyannote_hook(progress_cb: Any | None, base: float = 0.0, span: float = 1.0,
                   prefix: str = ""):
    """Hook de pyannote -> avance en [base, base+span] (se compone con el del pipeline).
    `prefix` se antepone al nombre del paso (p. ej. "tramo 2/9 · ")."""
    def _hook(step_name, step_artifact, file=None, total=None, completed=None, **_kw):
        if progress_cb is not None and total:
            try:
                frac = max(0.0, min(1.0, float(completed or 0) / float(total)))
                progress_cb(f"{prefix}{step_name}", base + span * frac, 1.0)
            except Exception:  # noqa: BLE001 — el progreso no debe tumbar la diarización
                pass
    return _hook


def diarize(media: bytes | Path, mime_type: str,
            progress_cb: Any | None = None) -> list[dict[str, Any]]:
    """Devuelve segmentos de hablante con start_ms, end_ms y label. Acepta bytes o ruta.

    `progress_cb(step_name, completed, total)` (opcional) informa el avance de pyannote.
    Audios largos se procesan POR TRAMOS (ver `_pyannote_diarization_chunked`)."""
    s = get_settings()
    wav_path = extract_audio_to_wav(media, mime_type)
    try:
        token = s.ASR_DIARIZATION_TOKEN or s.HF_TOKEN
        if not token:
            log.info("No hay HF_TOKEN; usando diarización por energía (fallback)")
            return _energy_based_diarization(wav_path)
        pipeline = _load_pipeline(token)
        chunk_s = int(getattr(s, "ASR_DIARIZATION_CHUNK_SECONDS", 900) or 0)
        try:
            info = sf.info(str(wav_path))
            dur_s = info.frames / float(info.samplerate or 16000)
        except Exception:  # noqa: BLE001
            dur_s = 0.0
        if chunk_s and dur_s > chunk_s:
            return _pyannote_diarization_chunked(wav_path, pipeline, chunk_s, progress_cb)
        return _pyannote_single(wav_path, pipeline, progress_cb)
    finally:
        wav_path.unlink(missing_ok=True)


def _pyannote_single(wav_path: Path, pipeline, progress_cb: Any | None) -> list[dict[str, Any]]:
    # pyannote 4 usa `torchcodec` para leer archivos; se le pasa la onda EN MEMORIA.
    waveform = _load_audio(wav_path, 16000)
    output = pipeline({"waveform": waveform, "sample_rate": 16000},
                      hook=_pyannote_hook(progress_cb))
    annotation = getattr(output, "exclusive_speaker_diarization", output)
    return [{"start_ms": int(t.start * 1000), "end_ms": int(t.end * 1000), "label": spk}
            for t, _, spk in annotation.itertracks(yield_label=True)]


def _pyannote_diarization_chunked(wav_path: Path, pipeline, chunk_s: int,
                                  progress_cb: Any | None) -> list[dict[str, Any]]:
    """Diariza por tramos y unifica hablantes por embeddings.

    pyannote sobre el audio completo es lentísimo y agota tiempo/memoria en audios de
    horas. Se procesa por tramos (con solape, para no perder hablantes en los cortes) y
    se agrupan los centroides de cada tramo (distancia coseno) para asignar una etiqueta
    GLOBAL: la misma voz recibe el mismo SPEAKER_xx en todos los tramos."""
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering

    s = get_settings()
    overlap_s = int(getattr(s, "ASR_DIARIZATION_OVERLAP_SECONDS", 10) or 0)
    threshold = float(getattr(s, "ASR_DIARIZATION_CLUSTER_THRESHOLD", 0.5) or 0.5)
    info = sf.info(str(wav_path))
    sr = int(info.samplerate or 16000)
    total_s = info.frames / float(sr)
    n_chunks = max(1, int((total_s + chunk_s - 1) // chunk_s))
    log.info("Diarización por tramos: %.0f s en %d tramo(s) de %d s", total_s, n_chunks, chunk_s)

    turns: list[dict[str, Any]] = []
    keys: list[tuple[int, str]] = []
    vectors: list[np.ndarray] = []
    for ci in range(n_chunks):
        core_start = ci * chunk_s
        core_end = min(core_start + chunk_s, total_s)
        read_end = min(core_end + overlap_s, total_s)
        n_frames = int((read_end - core_start) * sr)
        array, _ = sf.read(str(wav_path), start=int(core_start * sr), frames=n_frames,
                           dtype="float32", always_2d=True)
        waveform = torch.from_numpy(np.ascontiguousarray(array.T))  # (channels, time)
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        output = pipeline({"waveform": waveform, "sample_rate": sr},
                          hook=_pyannote_hook(progress_cb, base=ci / n_chunks, span=1.0 / n_chunks,
                                              prefix=f"tramo {ci + 1}/{n_chunks} · "))
        annotation = getattr(output, "exclusive_speaker_diarization", output)
        sd = getattr(output, "speaker_diarization", annotation)
        embs = getattr(output, "speaker_embeddings", None)
        if embs is not None and len(embs):
            for idx, spk in enumerate(list(sd.labels())):
                if idx < len(embs):
                    keys.append((ci, spk))
                    vectors.append(np.asarray(embs[idx], dtype=np.float32))
        log.info("Diarización tramo %d/%d: %d turno(s)", ci + 1, n_chunks,
                 sum(1 for _ in annotation.itertracks(yield_label=True)))
        for turn, _, spk in annotation.itertracks(yield_label=True):
            s_ms = int(max(core_start + turn.start, core_start) * 1000)
            e_ms = int(min(core_start + turn.end, core_end) * 1000)
            if e_ms - s_ms < 200:  # descarta migajas en el borde del tramo
                continue
            turns.append({"start_ms": s_ms, "end_ms": e_ms, "label": spk, "chunk": ci})

    # Clustering global de centroides. Se DESCARTAN los embeddings inválidos (NaN o norma 0):
    # pyannote produce NaN en tramos con segmentos vacíos ("Mean of empty slice") y sklearn
    # rechaza los NaN ("Input X contains NaN"). `nan_to_num` + filtro evitan el fallo.
    valid = [(k, np.nan_to_num(np.asarray(v, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0))
             for k, v in zip(keys, vectors)
             if np.all(np.isfinite(v)) and float(np.linalg.norm(np.asarray(v, dtype=np.float32))) > 0]
    key_to_global: dict[tuple[int, str], str] = {}
    if len(valid) >= 2:
        keys_v = [k for k, _ in valid]
        x = np.vstack([v for _, v in valid])
        x = x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)
        cl = AgglomerativeClustering(n_clusters=None, distance_threshold=threshold,
                                     metric="cosine", linkage="average")
        groups = cl.fit_predict(x)
        for k, g in zip(keys_v, groups):
            key_to_global[k] = f"SPEAKER_{int(g):02d}"
    elif valid:
        key_to_global[valid[0][0]] = "SPEAKER_00"
    elif keys:
        # Sin centroides válidos (todos NaN): no se puede unificar; al menos no perder turnos.
        for k in keys:
            key_to_global[k] = "SPEAKER_00"

    for t in turns:
        t["label"] = key_to_global.get((t["chunk"], t["label"]), "UNKNOWN")
        t.pop("chunk", None)
    turns.sort(key=lambda x: x["start_ms"])
    log.info("Diarización por tramos: %d turnos, %d hablante(s) global(es)",
             len(turns), len(set(key_to_global.values())))
    return turns
