"""Process Graph de un expediente: relaciones entre actuaciones + resolución de referenciados + revisión.

- `link_events`: construye `event_relationships` (refers_to / precede / appeals / responds_to) y
  marca `events.duplicate_of` cuando una actuación "referenciada" coincide con una real.
- `review_events`: marca `events.review_flags` (duplicado, fecha_inconsistente, sin_fuente_real, sin_fecha).
Ambas son deterministas e idempotentes (recalculan desde cero para el caso).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import one, rows

_APPEALS = ("apelacion", "reposicion", "queja", "casacion", "revision")
_RESPONDS = {
    "contestacion": ("auto_admisorio", "admision", "auto"),
    "concesion_recurso": ("apelacion",),
    "sentencia": ("audiencia", "auto_pruebas"),
    "reconvencion": ("auto_admisorio", "admision"),
    "recurso": ("sentencia", "auto"),
}


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def link_events(conn: Connection, org_id: str, case_id: str) -> dict[str, Any]:
    evs = rows(conn, """SELECT id, event_date, event_type, subtype, instance, date_type, description
                        FROM events WHERE case_id = :c AND kind = 'procedural'
                        ORDER BY event_date NULLS LAST""", c=case_id)
    conn.execute(text("DELETE FROM event_relationships WHERE case_id = :c"), {"c": case_id})
    conn.execute(text("UPDATE events SET duplicate_of = NULL WHERE case_id = :c AND kind = 'procedural'"),
                 {"c": case_id})

    real = [e for e in evs if e["date_type"] == "actuacion"]
    refs = [e for e in evs if e["date_type"] == "referenciada"]
    rel: list[tuple[str, str, str, float]] = []

    # (b) Resolver referenciados ↔ reales: mismo subtype (o tipo) y fecha cercana.
    for r in refs:
        best, best_score = None, 0.0
        rd = _date(r["event_date"])
        for e in real:
            score = 0.0
            if r["subtype"] and r["subtype"] == e["subtype"]:
                score += 0.6
            elif r["event_type"] and r["event_type"] == e["event_type"]:
                score += 0.3
            ed = _date(e["event_date"])
            if rd and ed:
                days = abs((rd - ed).days)
                score += 0.4 if days == 0 else (0.2 if days <= 45 else 0.0)
            score += 0.2 * _sim(r["description"], e["description"])
            if score > best_score:
                best, best_score = e, score
        if best is not None and best_score >= 0.5:
            rel.append((str(r["id"]), str(best["id"]), "refers_to", round(best_score, 3)))
            one(conn, "UPDATE events SET duplicate_of = :t WHERE id = :i RETURNING id",
                t=str(best["id"]), i=str(r["id"]))

    # (a) precede: actuaciones consecutivas por fecha dentro de la misma instancia.
    for inst in {e["instance"] for e in real}:
        seq = sorted([e for e in real if e["instance"] == inst],
                     key=lambda x: (x["event_date"] is None, x["event_date"]))
        for a, b in zip(seq, seq[1:], strict=False):
            rel.append((str(a["id"]), str(b["id"]), "precede", 0.7))

    # appeals / responds_to (heurística por subtipo y cercanía temporal hacia atrás).
    for e in real:
        rd = _date(e["event_date"])
        if not rd:
            continue
        if e["subtype"] in _APPEALS:
            prior = sorted([x for x in real if x["subtype"] in ("sentencia", "auto")
                            and _date(x["event_date"]) and _date(x["event_date"]) <= rd],
                           key=lambda x: _date(x["event_date"]))
            if prior:
                rel.append((str(e["id"]), str(prior[-1]["id"]), "appeals", 0.6))
        for target_sub in _RESPONDS.get(e["subtype"] or "", ()):
            prior = sorted([x for x in real if x["subtype"] == target_sub
                            and _date(x["event_date"]) and _date(x["event_date"]) <= rd],
                           key=lambda x: _date(x["event_date"]))
            if prior:
                rel.append((str(e["id"]), str(prior[-1]["id"]), "responds_to", 0.6))

    n = 0
    for s, t, r, conf in rel:
        if s == t:
            continue
        conn.execute(text("""INSERT INTO event_relationships
                (organization_id, case_id, source_event_id, target_event_id, relationship, confidence)
            VALUES (:o, :c, :s, :t, :r, :conf) ON CONFLICT DO NOTHING"""),
            {"o": org_id, "c": case_id, "s": s, "t": t, "r": r, "conf": conf})
        n += 1
    return {"events": len(evs), "relationships": n,
            "refers_to": sum(1 for x in rel if x[2] == "refers_to"),
            "precede": sum(1 for x in rel if x[2] == "precede"),
            "appeals": sum(1 for x in rel if x[2] == "appeals"),
            "responds_to": sum(1 for x in rel if x[2] == "responds_to")}


def review_events(conn: Connection, org_id: str, case_id: str) -> dict[str, Any]:
    evs = rows(conn, """SELECT id, event_date, event_type, subtype, date_type, description, duplicate_of
                        FROM events WHERE case_id = :c AND kind = 'procedural'""", c=case_id)
    conn.execute(text("UPDATE events SET review_flags = '[]'::jsonb WHERE case_id = :c AND kind = 'procedural'"),
                 {"c": case_id})

    buckets: dict[tuple, list] = defaultdict(list)
    for e in evs:
        if e["date_type"] == "actuacion" and e["subtype"]:
            buckets[(e["subtype"], str(e["event_date"]))].append(e)

    flags: dict[str, list[str]] = defaultdict(list)
    for group in buckets.values():
        if len(group) > 1:
            for e in group:
                flags[str(e["id"])].append("duplicado")

    by_id = {str(e["id"]): e for e in evs}
    for e in evs:
        if e["date_type"] == "referenciada":
            if not e["duplicate_of"]:
                flags[str(e["id"])].append("sin_fuente_real")
            else:
                src = by_id.get(str(e["duplicate_of"]))
                ed, sd = _date(e["event_date"]), _date(src["event_date"]) if src else None
                if ed and sd and abs((ed - sd).days) > 1:
                    flags[str(e["id"])].append("fecha_inconsistente")
        elif not e["event_date"]:
            flags[str(e["id"])].append("sin_fecha")

    for eid, f in flags.items():
        conn.execute(text("UPDATE events SET review_flags = CAST(:f AS jsonb) WHERE id = :i"),
                     {"f": json.dumps(sorted(set(f))), "i": eid})

    return {"events": len(evs), "flagged": len(flags),
            "duplicado": sum(1 for f in flags.values() if "duplicado" in f),
            "fecha_inconsistente": sum(1 for f in flags.values() if "fecha_inconsistente" in f),
            "sin_fuente_real": sum(1 for f in flags.values() if "sin_fuente_real" in f),
            "sin_fecha": sum(1 for f in flags.values() if "sin_fecha" in f)}
