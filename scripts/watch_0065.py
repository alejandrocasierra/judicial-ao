"""Vigila el 0065 hasta que termine ASR + diarización e imprime el resumen de
calidad (hablantes, conteo de segmentos, primeras/últimas intervenciones con minuto).

Cuando termina, el proceso sale y eso me notifica para verificar y lanzar los 8.
"""
from __future__ import annotations

import os
import sys
import time

import psycopg

DSN = os.environ.get("JUDICIAL_TEST_DSN") or (
    f"host={os.environ.get('POSTGRES_HOST', '127.0.0.1')} "
    f"port={os.environ.get('POSTGRES_PORT', '54322')} "
    f"dbname={os.environ.get('POSTGRES_DB', 'judicial')} "
    f"user={os.environ.get('POSTGRES_SUPERUSER', 'postgres')} "
    f"password={os.environ['POSTGRES_SUPERUSER_PASSWORD']}")
MEDIA_ID = "6930961d-ab78-4aa4-9dd3-0e074d5ea412"  # 0065 (prueba de estrés)
MAX_HOURS = 4


def mmss(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def main() -> int:
    deadline = time.time() + MAX_HOURS * 3600
    last = None
    while time.time() < deadline:
        with psycopg.connect(DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.processing_status,
                           (SELECT count(*) FROM transcript_segments s WHERE s.media_id = m.id) AS segs,
                           (SELECT count(*) FROM speakers sp WHERE sp.media_id = m.id) AS spk,
                           (SELECT p.pct FROM media_asr_progress p WHERE p.media_id = m.id) AS pct,
                           (SELECT j.status FROM jobs j
                             WHERE j.job_type='media_diarize' AND j.input_ids @> ARRAY[m.id]::uuid[]
                               AND j.created_at > now() - interval '4 hours'
                             ORDER BY j.created_at DESC LIMIT 1) AS diar_job,
                           (SELECT j.status FROM jobs j
                             WHERE j.job_type='file_ingest' AND j.input_ids @> ARRAY[m.id]::uuid[]
                             ORDER BY j.created_at DESC LIMIT 1) AS asr_job
                      FROM media m WHERE m.id = %s
                """, (MEDIA_ID,))
                row = cur.fetchone()
        if row is None:
            print("[watcher] el media ya no existe; se detiene", flush=True)
            return 1
        status, segs, spk, pct, diar_job, asr_job = row
        state = f"status={status} segs={segs} spk={spk} pct={pct} asr_job={asr_job} diar_job={diar_job}"
        if state != last:
            print(f"[watcher] {time.strftime('%H:%M:%S')} {state}", flush=True)
            last = state

        done = (diar_job in ("SUCCEEDED", "FAILED") or
                (diar_job is None and status in ("ASR_COMPLETE", "REVIEW_REQUIRED", "FAILED")
                 and asr_job in ("SUCCEEDED", "FAILED", None)))
        if done and segs:
            break
        if asr_job == "FAILED":
            print("[watcher] el ASR FALLÓ", flush=True)
            break
        time.sleep(45)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT coalesce(sp.display_name, sp.label) AS name, count(*) AS n,
                   min(s.start_ms) AS first_ms
              FROM transcript_segments s JOIN speakers sp ON sp.id = s.speaker_id
             WHERE s.media_id = %s GROUP BY 1 ORDER BY n DESC
        """, (MEDIA_ID,))
        print("\n=== HABLANTES ===", flush=True)
        for name, n, first_ms in cur.fetchall():
            print(f"  {name}: {n} intervenciones (primera en {mmss(first_ms or 0)})", flush=True)
        cur.execute("""
            SELECT s.start_ms, s.end_ms, coalesce(sp.display_name, sp.label), s.confidence, s.needs_review, s.text
              FROM transcript_segments s JOIN speakers sp ON sp.id = s.speaker_id
             WHERE s.media_id = %s ORDER BY s.start_ms
        """, (MEDIA_ID,))
        segs = cur.fetchall()
        print(f"\n=== TOTAL: {len(segs)} segmentos ===", flush=True)
        for label, rows in (("PRIMEROS", segs[:5]), ("ÚLTIMOS", segs[-5:])):
            print(f"\n=== {label} ===", flush=True)
            for start_ms, end_ms, name, conf, rev, text in rows:
                flag = " [REVISAR]" if rev else ""
                print(f"  {mmss(start_ms)}–{mmss(end_ms)} {name} (conf {float(conf or 0):.2f}){flag}: {(text or '')[:110]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
