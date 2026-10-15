"""Partición de segmentos con DOS VOCES (habla superpuesta) por análisis de locutor.

Problema real de las audiencias: dos personas hablan a la vez (p. ej. el interviniente
mostrando su cédula y la jueza pidiéndole el nombre al mismo tiempo). Whisper produce
UN segmento con las dos frases y pyannote ve UN solo turno → el segmento queda con un
solo hablante y la mitad del texto atribuida a la persona equivocada.

Solución: analizar la VOZ dentro del segmento.
1. Se enumeran puntos de corte candidatos entre palabras (pausas >= 0.25 s o fin de
   frase), usando los timestamps por palabra del ASR.
2. En cada candidato se extraen embeddings de locutor de las ventanas anterior y
   posterior (pyannote/embedding), leyendo SOLO esas ventanas del audio.
3. Si la distancia coseno entre ambas ventanas supera el umbral → son DOS voces: se
   parte el segmento en el candidato más distante.
4. Cada mitad se asigna al hablante más parecido (centroides de voz ya nombrados).

Sin timestamps por palabra o sin centroides, no se hace nada (aplica el flujo normal).
"""
from __future__ import annotations

import logging
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Distancia coseno entre las voces de las dos mitades para considerarlas distintas.
# pyannote/embedding: misma voz ~0.05-0.30; voces distintas ~0.45-0.85.
_VOICE_CHANGE_THRESHOLD = 0.45
_MIN_SEGMENT_MS = 2_000        # segmentos más cortos no se analizan
_WINDOW_MS = 1_500             # ventana de audio a cada lado del corte
_SKIP_MS = 300                 # se salta el borde (evita mezcla en el punto exacto)
_MIN_WINDOW_MS = 400           # ventana mínima útil
_PAUSE_MS = 250                # pausa entre palabras que habilita un candidato
_MAX_REF_DIST = 0.40           # la mitad debe parecerse a una voz conocida


@lru_cache(maxsize=1)
def _load_inference(token: str) -> Any:
    """Carga (una sola vez por proceso) la inferencia de embeddings de locutor."""
    from pyannote.audio import Inference, Model  # noqa: PLC0415
    model = Model.from_pretrained("pyannote/embedding", token=token)
    return Inference(model, window="whole")


def _norm(name: str) -> str:
    base = unicodedata.normalize("NFKD", (name or "").lower())
    return " ".join("".join(c for c in base if not unicodedata.combining(c)).split())


def _embed_pair(inference: Any, wav_path: Path, sr: int, start_ms: int, end_ms: int,
                cut_ms: int) -> tuple[Any, Any] | None:
    """Embeddings de las ventanas a cada lado del corte, leyendo solo esas muestras."""
    import numpy as np
    import soundfile as sf

    a0 = max(start_ms, cut_ms - _WINDOW_MS)
    a1 = cut_ms - _SKIP_MS
    b0 = cut_ms + _SKIP_MS
    b1 = min(end_ms, cut_ms + _WINDOW_MS)
    if a1 - a0 < _MIN_WINDOW_MS or b1 - b0 < _MIN_WINDOW_MS:
        return None
    try:
        wa, _ = sf.read(str(wav_path), start=int(a0 * sr / 1000),
                        frames=int((a1 - a0) * sr / 1000), dtype="float32", always_2d=False)
        wb, _ = sf.read(str(wav_path), start=int(b0 * sr / 1000),
                        frames=int((b1 - b0) * sr / 1000), dtype="float32", always_2d=False)
    except Exception:  # noqa: BLE001
        return None
    if len(wa) < 800 or len(wb) < 800:
        return None
    try:
        ea = inference(wa)
        eb = inference(wb)
    except Exception:  # noqa: BLE001
        return None
    va = np.asarray(ea, dtype="float32").reshape(-1)
    vb = np.asarray(eb, dtype="float32").reshape(-1)
    if va.size == 0 or vb.size == 0:
        return None
    na, nb = float(np.linalg.norm(va)) or 1.0, float(np.linalg.norm(vb)) or 1.0
    return va / na, vb / nb


def split_by_voice_change(
    media_path: Path,
    segments: list[dict[str, Any]],
    centroids: dict[str, list[float]] | None,
    label_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Parte en dos los segmentos que contienen DOS voces (habla superpuesta).

    `segments`: [{id, start_ms, end_ms, text, words, confidence, speaker_id, name, label}]
    Devuelve la misma lista, con piezas nuevas (`id=None`) cuando hubo partición y
    `split_reason='voice_change'` en ellas (name/label ya resueltos por voz)."""
    if not segments or not centroids:
        return segments
    import numpy as np
    import soundfile as sf

    from app.core.config import get_settings  # noqa: PLC0415
    s = get_settings()
    token = s.ASR_DIARIZATION_TOKEN or s.HF_TOKEN
    if not token:
        return segments

    # Referencias de voz normalizadas, con su nombre.
    refs: list[tuple[str, str, "np.ndarray"]] = []
    for label, vec in centroids.items():
        name = label_names.get(label)
        if not name:
            continue
        v = np.asarray(vec, dtype="float32").reshape(-1)
        if v.size == 0:
            continue
        refs.append((label, name, v / (float(np.linalg.norm(v)) or 1.0)))
    if len(refs) < 2:
        return segments

    idx_candidates = [i for i, sg in enumerate(segments)
                      if sg.get("words") and (sg["end_ms"] - sg["start_ms"]) >= _MIN_SEGMENT_MS]
    if not idx_candidates:
        return segments
    try:
        inference = _load_inference(token)
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo cargar el modelo de embeddings de locutor: %s", exc)
        return segments
    try:
        sr = int(sf.info(str(media_path)).samplerate or 16000)
    except Exception:  # noqa: BLE001
        return segments

    def _best_ref(vec: Any) -> tuple[str, str, float]:
        best = ("", "", 9.9)
        for label, name, r in refs:
            d = float(1.0 - float(np.dot(vec, r)))
            if d < best[2]:
                best = (label, name, d)
        return best

    out: list[dict[str, Any]] = []
    for i, sg in enumerate(segments):
        if i not in idx_candidates:
            out.append(sg)
            continue
        words = sg["words"]
        cands: list[int] = []
        for j in range(len(words) - 1):
            w_end = int(words[j].get("end_ms") or 0)
            n_start = int(words[j + 1].get("start_ms") or 0)
            txt = str(words[j].get("word") or "").strip()
            if ((n_start - w_end) >= _PAUSE_MS or txt.endswith((".", "?", "!"))) \
                    and sg["start_ms"] + 600 <= w_end <= sg["end_ms"] - 600:
                cands.append(w_end)
        if not cands:
            out.append(sg)
            continue
        best: tuple[float, int, Any, Any] | None = None
        for cut in cands:
            pair = _embed_pair(inference, media_path, sr, sg["start_ms"], sg["end_ms"], cut)
            if not pair:
                continue
            va, vb = pair
            dist = float(1.0 - float(np.dot(va, vb)))
            if best is None or dist > best[0]:
                best = (dist, cut, va, vb)
        if best is None or best[0] < _VOICE_CHANGE_THRESHOLD:
            out.append(sg)
            continue
        dist, cut, va, vb = best
        label_a, name_a, d_a = _best_ref(va)
        label_b, name_b, d_b = _best_ref(vb)
        if (not name_a or not name_b or _norm(name_a) == _norm(name_b)
                or d_a > _MAX_REF_DIST or d_b > _MAX_REF_DIST):
            out.append(sg)
            continue
        left = [w for w in words if int(w.get("end_ms") or 0) <= cut]
        right = [w for w in words if int(w.get("start_ms") or 0) >= cut]
        if not left or not right:
            out.append(sg)
            continue
        t1 = " ".join(str(w.get("word", "")).strip() for w in left).strip()
        t2 = " ".join(str(w.get("word", "")).strip() for w in right).strip()
        log.info("VOZ: el segmento %d–%d s tiene DOS voces (dist %.2f): %r (%s) | %r (%s)",
                 sg["start_ms"] // 1000, sg["end_ms"] // 1000, dist, t1[:45], name_a, t2[:45], name_b)
        out.append({**sg, "end_ms": cut, "text": t1, "words": left,
                    "name": name_a, "label": label_a, "split_reason": "voice_change"})
        out.append({**sg, "id": None, "start_ms": cut, "text": t2, "words": right,
                    "name": name_b, "label": label_b, "split_reason": "voice_change"})
    return out
