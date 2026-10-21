#!/usr/bin/env python3
"""Exporta y refresca el corpus de intención del router de chat.

El router (`app/services/intent.py`) se decide con regex. Para saber si acierta
con la forma de hablar de los usuarios reales hace falta un corpus etiquetado
por personas, no por el propio router.

Tres usos:

1. Preguntas REALES de la base (lo que hace falta para medir de verdad):

       python scripts/export_intent_corpus.py --org-id <uuid> --out evals/intent/corpus.csv

   Deja `label` VACÍA: la etiqueta la pone una persona. `suggested_label` trae
   lo que hoy decide el router, así etiquetar es confirmar o corregir.

2. Semilla desde el set dorado (`label` ya verificado en el YAML):

       python scripts/export_intent_corpus.py --from-golden evals/golden/chat_pipeline.yaml --append

3. Recalcular sugerencias sin tocar las etiquetas humanas:

       python scripts/export_intent_corpus.py --refresh --out evals/intent/corpus.csv

   Úsalo después de cambiar el router: `label` y `notes` son intocables; solo se
   reescriben `suggested_label`, `confidence` y `reason`.

Columnas del CSV:

- `label`            etiqueta HUMANA (vacía = pendiente; el eval la ignora)
- `needs_history`    `si` si la pregunta solo se entiende con el turno anterior
- `suggested_label`  lo que decide el router hoy (solo informativo)
- `has_attachments`  había adjuntos "@" en el turno (activa la regla `lectura`)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

FIELDS = [
    "id",
    "question",
    "label",
    "needs_history",
    "suggested_label",
    "confidence",
    "reason",
    "has_attachments",
    "created_at",
    "notes",
    # Procedencia: sin esto no se puede separar «lo que dijo un usuario real» de
    # «lo que escribió un agente» al medir. `labeler`: user > human > agent.
    "source",
    "labeler",
]

INTENTS = ("dato", "navegar", "analisis", "procedimental", "agregacion", "lectura", "correccion")


def _intent_mod():
    from app.services import intent

    return intent


def _norm(text: str) -> str:
    from app.services import text_terms

    return text_terms.norm(text).strip()


def _suggest(question: str, has_attachments: bool) -> tuple[str, float, str]:
    intent = _intent_mod()
    decision = intent.classify(question, has_attachments=has_attachments)
    value = str(getattr(decision.intent, "value", decision.intent))
    return value, float(decision.confidence), str(decision.reason)


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = [{k: (v or "") for k, v in row.items() if k in FIELDS} for row in reader]
    for row in rows:
        for field in FIELDS:
            row.setdefault(field, "")
    return rows


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


SOURCE_PRIORITY = {"real": 3, "golden": 2, "seed": 1, "": 0}


def _priority(row: dict[str, str]) -> int:
    """Real > golden > seed; una etiqueta de usuario manda sobre cualquier agente."""
    return (2 if _clean(row.get("labeler")) == "user" else 0) + SOURCE_PRIORITY.get(
        _clean(row.get("source")), 0)


def _merge(existing: list[dict[str, str]], incoming: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Fusiona por pregunta normalizada: nunca pisa una `label` humana.

    Si la misma pregunta existe con distinta procedencia, gana la más fiable
    (real > golden > seed) para que la medición no confunda una pregunta real
    con la versión sintética.
    """
    by_key = {_norm(row["question"]): row for row in existing if _clean(row.get("question"))}
    added = 0
    for row in incoming:
        key = _norm(row["question"])
        current = by_key.get(key)
        if current is None:
            by_key[key] = row
            added += 1
            continue
        if _priority(row) > _priority(current):
            # Reemplaza la fila, pero conserva la etiqueta humana si la tenía.
            merged = dict(row)
            if _clean(current.get("labeler")) in ("human", "user") and _clean(current.get("label")):
                merged["label"] = current["label"]
                merged["labeler"] = current["labeler"]
                merged["notes"] = current.get("notes") or merged.get("notes", "")
            by_key[key] = merged
            continue
        # La fila ya existía: conservar etiqueta/notas y refrescar la sugerencia.
        current["suggested_label"] = row.get("suggested_label", current.get("suggested_label", ""))
        current["confidence"] = row.get("confidence", current.get("confidence", ""))
        current["reason"] = row.get("reason", current.get("reason", ""))
        if not current.get("label") and row.get("label"):
            current["label"] = row["label"]
            current["labeler"] = row.get("labeler", current.get("labeler", ""))
            current["source"] = row.get("source", current.get("source", ""))
        if not current.get("notes") and row.get("notes"):
            current["notes"] = row["notes"]
    return list(by_key.values()), added


def _from_db(org_id: str, user_id: str | None, case_id: str | None, days: int,
             limit: int, min_chars: int) -> list[dict[str, str]]:
    from app.core.db import rows as db_rows
    from app.core.db import tx

    sql = """
        SELECT m.id::text AS id, m.content, m.attachments, m.created_at::text AS created_at
          FROM chat_messages m
          JOIN chat_sessions s ON s.id = m.session_id
         WHERE m.role = 'user'
           AND length(btrim(m.content)) >= :min_chars
           AND m.created_at >= now() - make_interval(days => :days)
    """
    params: dict[str, Any] = {"min_chars": min_chars, "days": days, "limit": limit}
    if case_id:
        sql += " AND s.case_id = CAST(:case_id AS uuid)"
        params["case_id"] = case_id
    sql += " ORDER BY m.created_at DESC LIMIT :limit"

    with tx(org_id, user_id) as conn:
        raw = db_rows(conn, sql, **params)

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        question = _clean(item.get("content"))
        key = _norm(question)
        if not key or key in seen:
            continue
        seen.add(key)
        has_attachments = bool(item.get("attachments"))
        suggested, confidence, reason = _suggest(question, has_attachments)
        out.append({
            "id": str(item.get("id") or ""),
            "question": question,
            "label": "",
            "needs_history": "no",
            "suggested_label": suggested,
            "confidence": f"{confidence:.2f}",
            "reason": reason,
            "has_attachments": "si" if has_attachments else "no",
            "created_at": str(item.get("created_at") or ""),
            "notes": "",
            "source": "real",
            "labeler": "",
        })
    return out


def _from_feedback(org_id: str, days: int, limit: int) -> list[dict[str, str]]:
    """Etiquetas VERDADERAS del usuario (accepted / wrong_intent), si las hay."""
    from app.core.db import rows as db_rows
    from app.core.db import tx

    sql = """
        SELECT f.message_id::text AS id, f.verdict, f.corrected_intent,
               m.intent AS router_intent, q.content AS question,
               f.created_at::text AS created_at, q.attachments
          FROM intent_feedback f
          JOIN chat_messages m ON m.id = f.message_id
          JOIN LATERAL (
                 SELECT content, attachments FROM chat_messages u
                  WHERE u.session_id = m.session_id AND u.role = 'user'
                    AND u.created_at <= m.created_at
                  ORDER BY u.created_at DESC LIMIT 1
               ) q ON true
         WHERE f.organization_id = :o
           AND f.created_at >= now() - make_interval(days => :days)
         ORDER BY f.created_at DESC LIMIT :limit
    """
    with tx(org_id) as conn:
        raw = db_rows(conn, sql, o=org_id, days=days, limit=limit)
    out: list[dict[str, str]] = []
    for item in raw:
        verdict = str(item.get("verdict") or "")
        label = (str(item.get("router_intent") or "") if verdict == "accepted"
                 else str(item.get("corrected_intent") or "") if verdict == "wrong_intent"
                 else "")
        question = _clean(item.get("question"))
        if not question or label not in INTENTS:
            continue
        out.append({
            "id": str(item.get("id") or ""),
            "question": question,
            "label": label,
            "needs_history": "no",
            "suggested_label": "",
            "confidence": "",
            "reason": "",
            "has_attachments": "si" if item.get("attachments") else "no",
            "created_at": str(item.get("created_at") or ""),
            "notes": f"feedback:{verdict}",
            "source": "real",
            "labeler": "user",
        })
    return out


def _from_golden(path: Path) -> list[dict[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[dict[str, str]] = []
    for case in data.get("cases") or []:
        question = _clean(case.get("question"))
        label = _clean(case.get("intent")).lower()
        if not question or label not in INTENTS:
            continue
        suggested, confidence, reason = _suggest(question, False)
        out.append({
            "id": str(case.get("id") or ""),
            "question": question,
            "label": label,
            "needs_history": "no",
            "suggested_label": suggested,
            "confidence": f"{confidence:.2f}",
            "reason": reason,
            "has_attachments": "no",
            "created_at": "",
            "notes": f"golden:{case.get('id')}",
            "source": "golden",
            "labeler": "human",
        })
    return out


def _refresh(rows: list[dict[str, str]]) -> int:
    changed = 0
    for row in rows:
        question = _clean(row.get("question"))
        if not question:
            continue
        has_attachments = _clean(row.get("has_attachments")).lower() in ("si", "sí", "true", "1", "yes")
        suggested, confidence, reason = _suggest(question, has_attachments)
        if (suggested, row.get("confidence"), reason) != (row.get("suggested_label"),
                                                          f"{confidence:.2f}", row.get("reason")):
            changed += 1
        row["suggested_label"] = suggested
        row["confidence"] = f"{confidence:.2f}"
        row["reason"] = reason
    return changed


def _summary(rows: list[dict[str, str]]) -> str:
    labeled = [r for r in rows if _clean(r.get("label"))]
    pending = len(rows) - len(labeled)
    dist: dict[str, int] = {}
    for row in labeled:
        dist[row["label"]] = dist.get(row["label"], 0) + 1
    parts = [f"{k}={v}" for k, v in sorted(dist.items())]
    return f"{len(rows)} preguntas · {len(labeled)} etiquetadas · {pending} pendientes · {', '.join(parts) or 'sin etiquetas'}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Exporta el corpus de intención para etiquetar a mano.")
    p.add_argument("--org-id", default=None, help="Organización cuyas conversaciones se exportan (RLS)")
    p.add_argument("--user-id", default=None, help="Actor para la traza de la transacción")
    p.add_argument("--case-id", default=None, help="Limitar a un expediente")
    p.add_argument("--days", type=int, default=180, help="Ventana temporal en días (default 180)")
    p.add_argument("--limit", type=int, default=2000, help="Máximo de mensajes leídos (default 2000)")
    p.add_argument("--min-chars", type=int, default=8, help="Descartar mensajes más cortos (default 8)")
    p.add_argument("--from-golden", default=None, help="Sembrar desde un YAML del set dorado")
    p.add_argument("--from-feedback", action="store_true",
                   help="Añadir las etiquetas verdaderas que dejaron los usuarios (intent_feedback)")
    p.add_argument("--out", default="evals/intent/corpus.csv")
    p.add_argument("--append", action="store_true", help="Fusionar con el CSV existente sin pisar etiquetas")
    p.add_argument("--refresh", action="store_true", help="Solo recalcular sugerencias del CSV existente")
    args = p.parse_args()

    env_path = Path(os.environ.get("ENV_FILE") or (_ROOT / ".env"))
    if env_path.exists():
        import envload

        envload.load(str(env_path), override=bool(os.environ.get("ENV_FILE")))

    out = Path(args.out)
    if not out.is_absolute():
        out = _ROOT / out

    if args.refresh:
        rows = _read_csv(out)
        if not rows:
            print(f"[ERROR] no existe corpus en {out}")
            return 2
        changed = _refresh(rows)
        _write_csv(out, rows)
        print(f"[OK] {out}: {changed} sugerencias actualizadas de {len(rows)}")
        print(f"     {_summary(rows)}")
        return 0

    if args.from_golden:
        incoming = _from_golden(_ROOT / args.from_golden)
    elif args.from_feedback:
        if not args.org_id:
            print("[ERROR] --from-feedback necesita --org-id")
            return 2
        incoming = _from_feedback(args.org_id, args.days, args.limit)
    else:
        if not args.org_id:
            print("[ERROR] falta --org-id (o usa --from-golden para sembrar sin base)")
            return 2
        incoming = _from_db(args.org_id, args.user_id, args.case_id, args.days,
                            args.limit, args.min_chars)

    if not incoming:
        print("[WARN] no se encontraron preguntas. Nada que escribir.")
        print("       Si la base no tiene conversaciones, siembra con --from-golden.")
        return 0

    existing = _read_csv(out) if args.append else []
    merged, added = _merge(existing, incoming)
    _write_csv(out, merged)
    print(f"[OK] {out}: {added} nuevas de {len(incoming)} leídas")
    print(f"     {_summary(merged)}")
    print("     Siguiente paso: rellenar la columna `label` y correr scripts/eval_intent.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
