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

from app.providers.asr import extract_audio_to_wav, _fmt_ms
from app.core.config import get_settings

log = logging.getLogger(__name__)


def _torch():
    """Import perezoso de torch: el worker Celery NO debe cargar ~1 GB de torch en
    reposo; solo se carga cuando la diarización realmente corre."""
    import torch  # noqa: PLC0415
    return torch


def _torchaudio():
    import torchaudio  # noqa: PLC0415
    return torchaudio


def _load_audio(wav_path: Path, sample_rate: int = 16000) -> Any:
    # soundfile evita incompatibilidades entre torchaudio 2.14 y soundfile 0.13
    # (torchaudio.load pasa argumentos que soundfile no acepta).
    torch = _torch()
    torchaudio = _torchaudio()
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
    torchaudio = _torchaudio()
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
    torch = _torch()
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
            except Exception as exc:  # noqa: BLE001 — el progreso no debe tumbar la diarización
                log.debug("progress_cb de diarización falló: %s", exc)
    return _hook


def diarize(media: bytes | Path, mime_type: str,
            progress_cb: Any | None = None,
            visual_names: list[dict[str, Any]] | None = None,
            return_centroids: bool = False) -> Any:
    """Devuelve segmentos de hablante con start_ms, end_ms y label. Acepta bytes o ruta.

    `progress_cb(step_name, completed, total)` (opcional) informa el avance de pyannote.
    Audios largos se procesan POR TRAMOS (ver `_pyannote_diarization_chunked`).
    `visual_names` (opcional): muestras del nombre resaltado de Teams; se usan como
    SEMILLAS para unir fragmentos de la misma voz entre tramos (nombre inequívoco).
    `return_centroids=True`: devuelve (turns, centroides por label) — los centroides de
    voz permiten partir segmentos con DOS voces (habla superpuesta) y asignar cada
    mitad al hablante más parecido."""
    s = get_settings()
    wav_path = extract_audio_to_wav(media, mime_type)
    try:
        token = s.ASR_DIARIZATION_TOKEN or s.HF_TOKEN
        if not token:
            log.info("No hay HF_TOKEN; usando diarización por energía (fallback)")
            turns = _energy_based_diarization(wav_path)
            return (turns, {}) if return_centroids else turns
        pipeline = _load_pipeline(token)
        chunk_s = int(getattr(s, "ASR_DIARIZATION_CHUNK_SECONDS", 900) or 0)
        try:
            info = sf.info(str(wav_path))
            dur_s = info.frames / float(info.samplerate or 16000)
        except Exception:  # noqa: BLE001
            dur_s = 0.0
        if chunk_s and dur_s > chunk_s:
            return _pyannote_diarization_chunked(wav_path, pipeline, chunk_s, progress_cb,
                                                 visual_names=visual_names,
                                                 return_centroids=return_centroids)
        turns = _pyannote_single(wav_path, pipeline, progress_cb)
        return (turns, {}) if return_centroids else turns
    finally:
        wav_path.unlink(missing_ok=True)


def _pyannote_single(wav_path: Path, pipeline, progress_cb: Any | None) -> list[dict[str, Any]]:
    # pyannote 4 usa `torchcodec` para leer archivos; se le pasa la onda EN MEMORIA.
    waveform = _load_audio(wav_path, 16000)
    output = pipeline({"waveform": waveform, "sample_rate": 16000},
                      hook=_pyannote_hook(progress_cb))
    annotation = getattr(output, "exclusive_speaker_diarization", output)
    turns = [{"start_ms": int(t.start * 1000), "end_ms": int(t.end * 1000), "label": spk}
             for t, _, spk in annotation.itertracks(yield_label=True)]
    if bool(getattr(get_settings(), "ASR_LOG_LIVE", False)):
        for t in turns:
            log.info("DIAR [%s–%s] %s", _fmt_ms(t["start_ms"]), _fmt_ms(t["end_ms"]), t["label"])
    return turns


def _seed_names_from_visual(turns: list[dict[str, Any]], keys: list[tuple[int, str]],
                            visual_names: list[dict[str, Any]] | None,
                            min_share: float = 0.7, min_samples: int = 2) -> dict[tuple[int, str], str]:
    """Nombre SEMILLA por (tramo, hablante local) desde el nombre resaltado de Teams.

    Solo cuando UN nombre domina claramente (>=70 % de las muestras y al menos 2):
    si Teams resalta DOS nombres a la vez (recuadro morado en varios participantes),
    no hay semilla y ese tramo no se usa para unir voces. Devuelve {(chunk, spk): nombre}."""
    if not visual_names:
        return {}
    from collections import Counter
    seeds: dict[tuple[int, str], str] = {}
    for key in keys:
        ci, spk = key
        names: list[str] = []
        for t in turns:
            if t["chunk"] != ci or t["label"] != spk:
                continue
            names += [e["name"] for e in visual_names
                      if t["start_ms"] <= e["timestamp_ms"] <= t["end_ms"]]
        if len(names) < min_samples:
            continue
        counter = Counter(names)
        top, cnt = counter.most_common(1)[0]
        runner_up = counter.most_common(2)[1][1] if len(counter) > 1 else 0
        if cnt / len(names) >= min_share and runner_up / len(names) < 0.25:
            seeds[key] = top
    return seeds


def _pyannote_diarization_chunked(wav_path: Path, pipeline, chunk_s: int,
                                  progress_cb: Any | None,
                                  visual_names: list[dict[str, Any]] | None = None,
                                  return_centroids: bool = False) -> Any:
    """Diariza por tramos y unifica hablantes por embeddings.

    pyannote sobre el audio completo es lentísimo y agota tiempo/memoria en audios de
    horas. Se procesa por tramos (con solape, para no perder hablantes en los cortes) y
    se agrupan los centroides de cada tramo (distancia coseno) para asignar una etiqueta
    GLOBAL: la misma voz recibe el mismo SPEAKER_xx en todos los tramos."""
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering
    torch = _torch()

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
        log_live = bool(getattr(s, "ASR_LOG_LIVE", False))
        for turn, _, spk in annotation.itertracks(yield_label=True):
            s_ms = int(max(core_start + turn.start, core_start) * 1000)
            e_ms = int(min(core_start + turn.end, core_end) * 1000)
            if e_ms - s_ms < 200:  # descarta migajas en el borde del tramo
                continue
            if log_live:
                log.info("DIAR [%s–%s] %s", _fmt_ms(s_ms), _fmt_ms(e_ms), spk)
            turns.append({"start_ms": s_ms, "end_ms": e_ms, "label": spk, "chunk": ci})

    # Clustering global. Si hay SEMILLAS visuales (un nombre inequívoco por voz en algún
    # tramo), se usan para UNIR fragmentos de la misma persona en todo el video: todos
    # los centroides con la misma semilla forman un hablante, y los centroides sin
    # semilla se pegan al grupo sembrado más cercano (o se agrupan entre ellos).
    #
    # Se DESCARTAN los embeddings inválidos (NaN o norma 0): pyannote produce NaN en
    # tramos con segmentos vacíos ("Mean of empty slice") y sklearn rechaza los NaN.
    valid = [(k, np.nan_to_num(np.asarray(v, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0))
             for k, v in zip(keys, vectors, strict=False)
             if np.all(np.isfinite(v)) and float(np.linalg.norm(np.asarray(v, dtype=np.float32))) > 0]
    key_to_global: dict[tuple[int, str], str] = {}
    seed_names = _seed_names_from_visual(turns, keys, visual_names)
    if seed_names:
        log.info("Diarización: %d semilla(s) de nombre desde Teams: %s",
                 len({v for v in seed_names.values()}), sorted(set(seed_names.values())))
    if valid and seed_names:
        x = np.vstack([v for _, v in valid])
        x = x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)
        vec_of = {k: v for (k, _), v in zip(valid, x, strict=False)}
        # Grupos por semilla (un hablante global por nombre inequívoco).
        label_for_seed: dict[str, str] = {}
        centroids: dict[str, list[np.ndarray]] = {}
        for k, name in seed_names.items():
            if k not in vec_of:
                continue
            label_for_seed.setdefault(name, f"SPEAKER_{len(label_for_seed):02d}")
            key_to_global[k] = label_for_seed[name]
            centroids.setdefault(name, []).append(vec_of[k])
        # Centroides sin semilla: al grupo sembrado más cercano (coseno) o entre ellos.
        rest_keys = [k for k, _ in valid if k not in key_to_global]
        if rest_keys:
            rest_x = np.vstack([vec_of[k] for k in rest_keys])
            if centroids:
                seed_centroids = []
                seed_labels = []
                for name, vecs in centroids.items():
                    c = np.mean(vecs, axis=0)
                    seed_centroids.append(c / (np.linalg.norm(c) + 1e-9))
                    seed_labels.append(label_for_seed[name])
                sims = rest_x @ np.vstack(seed_centroids).T  # coseno (todo normalizado)
                best_idx = np.argmax(sims, axis=1)
                best_sim = sims[np.arange(len(rest_keys)), best_idx]
                unmatched: list[int] = []
                for i, k in enumerate(rest_keys):
                    if 1.0 - float(best_sim[i]) <= threshold:
                        key_to_global[k] = seed_labels[int(best_idx[i])]
                    else:
                        unmatched.append(i)
                if unmatched:
                    um_x = rest_x[unmatched]
                    if len(unmatched) >= 2:
                        cl = AgglomerativeClustering(n_clusters=None, distance_threshold=threshold,
                                                     metric="cosine", linkage="average")
                        groups = cl.fit_predict(um_x)
                    else:
                        groups = [0]
                    base = len(label_for_seed)
                    for i, g in zip(unmatched, groups, strict=False):
                        key_to_global[rest_keys[i]] = f"SPEAKER_{base + int(g):02d}"
    elif len(valid) >= 2:
        keys_v = [k for k, _ in valid]
        x = np.vstack([v for _, v in valid])
        x = x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)
        cl = AgglomerativeClustering(n_clusters=None, distance_threshold=threshold,
                                     metric="cosine", linkage="average")
        groups = cl.fit_predict(x)
        for k, g in zip(keys_v, groups, strict=False):
            key_to_global[k] = f"SPEAKER_{int(g):02d}"
    elif valid:
        key_to_global[valid[0][0]] = "SPEAKER_00"
    elif keys:
        # Sin centroides válidos (todos NaN): no se puede unificar; al menos no perder turnos.
        for k in keys:
            key_to_global[k] = "SPEAKER_00"

    seed_name_by_label = {label_for_seed[n]: n for n in label_for_seed} if seed_names else {}
    for t in turns:
        t["label"] = key_to_global.get((t["chunk"], t["label"]), "UNKNOWN")
        t.pop("chunk", None)
        nm = seed_name_by_label.get(t["label"])
        if nm:
            t["seed_name"] = nm
    turns.sort(key=lambda x: x["start_ms"])
    log.info("Diarización por tramos: %d turnos, %d hablante(s) global(es)",
             len(turns), len(set(key_to_global.values())))

    # Centroides de voz por hablante global (para partir segmentos con DOS voces).
    label_centroids: dict[str, list[float]] = {}
    if return_centroids:
        acc: dict[str, list[np.ndarray]] = {}
        for k, v in valid:
            lbl = key_to_global.get(k)
            if lbl:
                acc.setdefault(lbl, []).append(v)
        for lbl, vecs in acc.items():
            c = np.mean(np.vstack(vecs), axis=0)
            n = float(np.linalg.norm(c)) or 1.0
            label_centroids[lbl] = (c / n).tolist()
    return (turns, label_centroids, seed_name_by_label) if return_centroids else turns
