"""Proceso hijo de Whisper: protocolo JSON-lines por stdin/stdout.

El worker Celery NO puede crear hijos con multiprocessing (sus procesos son daemon);
por eso el hijo es un `subprocess` normal corriendo este módulo. El modelo (1.5–3 GB)
vive SOLO aquí: si el OOM killer mata a este proceso, muere solo él y el padre
(`_WhisperChild` en asr.py) lo recrea y reintenta el tramo más pequeño.

Protocolo:
  padre -> hijo: {"type":"init","model_kwargs":{...},"opts":{...}}
                 {"type":"chunk","wav":path,"start":frame,"frames":n}
                 {"type":"stop"}
  hijo -> padre: {"type":"ready"}
                 {"type":"seg","seg":{...},"frac":0.0-1.0}
                 {"type":"ok"} | {"type":"err","msg":...}
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    def send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    init = json.loads(sys.stdin.readline())
    model_kwargs = init["model_kwargs"]
    opts = init["opts"]

    import soundfile as sf  # noqa: PLC0415 — carga en el hijo, no en el worker
    from faster_whisper import WhisperModel  # noqa: PLC0415
    from app.providers.asr import _segment_to_dict  # noqa: PLC0415

    try:
        model = WhisperModel(**model_kwargs)
    except Exception:  # noqa: BLE001 — fallback a CPU int8 si el dispositivo falla
        model = WhisperModel(**{**model_kwargs, "device": "cpu", "compute_type": "int8"})
    send({"type": "ready"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg["type"] == "stop":
            break
        if msg["type"] != "chunk":
            continue
        try:
            data, _sr = sf.read(msg["wav"], start=msg["start"], frames=msg["frames"],
                                dtype="float32", always_2d=False)
            segments, info = model.transcribe(data, **opts)
            total = float(getattr(info, "duration", 0) or 0)
            for seg in segments:
                send({"type": "seg", "seg": _segment_to_dict(seg),
                      "frac": min(1.0, float(seg.end) / total) if total else 0.0})
            send({"type": "ok"})
        except Exception as exc:  # noqa: BLE001
            send({"type": "err", "msg": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
