#!/usr/bin/env python3
"""Exporta la transcripción de un medio en formato legible (estilo otter.ai).

Uso:

    python scripts/export_media_transcript.py --media-id <uuid> \
        --org-id <uuid> [--user-id <uuid>] [--out transcript.txt]

Salida por línea:  [mm:ss–mm:ss] SPEAKER (nombre visual si existe): texto
Las líneas con needs_review se marcan con "⚠ revisar".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    import envload  # noqa: E402

    envload.load(str(_env_path))

from sqlalchemy import text  # noqa: E402

from app.core.db import tx  # noqa: E402


def _fmt_ms(ms: int) -> str:
    total_s = ms // 1000
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


def main() -> int:
    p = argparse.ArgumentParser(description="Exporta transcripción de un medio")
    p.add_argument("--media-id", required=True)
    p.add_argument("--org-id", required=True)
    p.add_argument("--user-id", default=None)
    p.add_argument("--out", default=None, help="Archivo de salida (default: stdout)")
    args = p.parse_args()

    with tx(args.org_id, args.user_id) as conn:
        media = conn.execute(text(
            "SELECT filename, processing_status, duration_ms FROM media WHERE id = :m"
        ), {"m": args.media_id}).mappings().first()
        if media is None:
            print(f"media {args.media_id} no encontrado", file=sys.stderr)
            return 1
        rows = conn.execute(text("""
            SELECT ts.start_ms, ts.end_ms, ts.text, ts.confidence, ts.needs_review,
                   s.label AS speaker_label
            FROM transcript_segments ts
            LEFT JOIN speakers s ON s.id = ts.speaker_id
            WHERE ts.media_id = :m
            ORDER BY ts.start_ms
        """), {"m": args.media_id}).mappings().all()

    lines = [
        f"# Transcripción: {media['filename']}",
        f"# Estado: {media['processing_status']} · duración: {_fmt_ms(media['duration_ms'] or 0)}",
        f"# Segmentos: {len(rows)}",
        "",
    ]
    for r in rows:
        flag = " ⚠ revisar" if r["needs_review"] else ""
        lines.append(
            f"[{_fmt_ms(r['start_ms'])}–{_fmt_ms(r['end_ms'])}] "
            f"{r['speaker_label'] or 'UNKNOWN'} (conf {r['confidence']:.2f}){flag}: {r['text']}"
        )

    output = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Escrito: {args.out} ({len(rows)} segmentos)")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
