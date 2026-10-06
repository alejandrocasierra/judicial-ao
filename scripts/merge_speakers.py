#!/usr/bin/env python3
"""Fusiona dos hablantes duplicados (la misma persona detectada en dos clusters de diarización).

- Reasigna los segmentos del hablante `--merge` al hablante `--keep`.
- Borra el hablante duplicado.
- Reindexa en pgvector los audios afectados (para que los chunks lleven el nombre correcto).
- Reconstruye el grafo del caso (los nodos Speaker del duplicado desaparecen).

Uso:
    python scripts/merge_speakers.py --case-id <uuid> --org-id <uuid> \
        --keep-label SPEAKER_03 --merge-label SPEAKER_01 --dry-run
    python scripts/merge_speakers.py --case-id <uuid> --org-id <uuid> \
        --keep <speaker_id> --merge <speaker_id> [--user-id <uuid>]

En el VPS:
    docker compose --env-file .env.advisorlegal exec -T api \
      python /srv/scripts/merge_speakers.py --case-id ... --org-id ... --keep-label ... --merge-label ...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import case_transfer as ct  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Fusiona dos hablantes duplicados de un caso")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--keep", default=None, help="speaker_id que se conserva")
    ap.add_argument("--merge", default=None, help="speaker_id que se elimina (se reasigna a --keep)")
    ap.add_argument("--keep-label", default=None, help="alternativa a --keep (p. ej. SPEAKER_03)")
    ap.add_argument("--merge-label", default=None, help="alternativa a --merge (p. ej. SPEAKER_01)")
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--dry-run", action="store_true", help="muestra el plan y no cambia nada")
    a = ap.parse_args()

    conn = ct.connect(a.org_id)
    cur = conn.cursor()

    def resolve(idv: str | None, label: str | None, which: str) -> str:
        if idv:
            return idv
        if label:
            cur.execute("SELECT id FROM speakers WHERE case_id = %s AND label = %s", (a.case_id, label))
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"ERROR: no existe el hablante {label}")
            return str(row[0])
        raise SystemExit(f"ERROR: indica --{which} o --{which}-label")

    keep = resolve(a.keep, a.keep_label, "keep")
    merge = resolve(a.merge, a.merge_label, "merge")
    if keep == merge:
        raise SystemExit("ERROR: keep y merge son el mismo hablante")

    cur.execute("SELECT id, label, display_name FROM speakers WHERE case_id = %s AND id = ANY(%s)",
                (a.case_id, [keep, merge]))
    found = {str(r[0]): (r[1], r[2]) for r in cur.fetchall()}
    if keep not in found or merge not in found:
        raise SystemExit("ERROR: alguno de los hablantes no pertenece al caso")

    cur.execute("SELECT count(*), count(DISTINCT media_id) FROM transcript_segments WHERE speaker_id = %s", (merge,))
    n_seg, n_media = cur.fetchone()
    print(f"conservar : {found[keep][0]}  ({found[keep][1]})  id={keep}")
    print(f"fusionar  : {found[merge][0]}  ({found[merge][1]})  id={merge}")
    print(f"segmentos a reasignar: {n_seg}  en {n_media} media")

    if a.dry_run:
        print("\n[DRY-RUN] no se cambia nada. Quita --dry-run para aplicar.")
        conn.close()
        return 0

    cur.execute("UPDATE transcript_segments SET speaker_id = %s WHERE speaker_id = %s", (keep, merge))
    moved = cur.rowcount
    cur.execute("DELETE FROM speakers WHERE id = %s", (merge,))
    cur.execute("SELECT DISTINCT media_id FROM transcript_segments WHERE speaker_id = %s", (keep,))
    media_ids = [str(r[0]) for r in cur.fetchall()]
    conn.commit()
    print(f"[OK] reasignados {moved} segmentos y borrado el duplicado")

    from app.core.db import tx
    from app.services import graph, indexing

    done = 0
    for mid in media_ids:
        try:
            with tx(a.org_id, a.user_id) as c:
                indexing.index_media(c, a.org_id, a.case_id, mid, actor_id=a.user_id)
            done += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: no se pudo reindexar {mid}: {exc}")
    print(f"[OK] media reindexados: {done}/{len(media_ids)}")

    try:
        with tx(a.org_id, a.user_id) as c:
            res = graph.build_case_graph(c, a.org_id, a.case_id, actor_id=a.user_id)
        print(f"[OK] grafo reconstruido: {res}")
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: no se pudo reconstruir el grafo: {exc}")

    conn.close()
    print("\nListo. Recarga el modal: ahora debe aparecer un solo hablante.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
