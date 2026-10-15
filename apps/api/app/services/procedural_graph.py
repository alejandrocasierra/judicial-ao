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


def _absorb_event_citations(conn: Connection, org_id: str, case_id: str, from_event_id: str,
                            to_event_id: str) -> int:
    """Copia las citas de un evento referenciado al evento real, evitando duplicados exactos."""
    cites = rows(conn, """SELECT source_type, document_id, page_number, folio, media_id, segment_id,
                                 start_ms, end_ms, quote_hash
                          FROM citations
                          WHERE case_id = :c AND target_type = 'event' AND target_id = :src""",
                 c=case_id, src=from_event_id)
    n = 0
    for cit in cites:
        # Evita duplicados exactos: misma fuente apuntando ya al evento real.
        existing = one(conn, """SELECT id FROM citations
                                WHERE case_id = :c AND target_type = 'event' AND target_id = :to
                                  AND source_type = :st
                                  AND COALESCE(document_id, '00000000-0000-0000-0000-000000000000'::uuid) =
                                      COALESCE(:did, '00000000-0000-0000-0000-000000000000'::uuid)
                                  AND COALESCE(media_id, '00000000-0000-0000-0000-000000000000'::uuid) =
                                      COALESCE(:mid, '00000000-0000-0000-0000-000000000000'::uuid)
                                  AND COALESCE(page_number, -1) = COALESCE(:pn, -1)
                                  AND COALESCE(start_ms, -1) = COALESCE(:sms, -1)""",
                       c=case_id, to=to_event_id, st=cit["source_type"], did=cit.get("document_id"),
                       mid=cit.get("media_id"), pn=cit.get("page_number"), sms=cit.get("start_ms"))
        if existing:
            continue
        one(conn, """INSERT INTO citations
                       (organization_id, case_id, target_type, target_id, source_type,
                        document_id, page_number, folio, media_id, segment_id, start_ms, end_ms, quote_hash)
                     VALUES (:o, :c, 'event', :tid, :st, :did, :pn, :folio, :mid, :sid, :sms, :ems, :qh)
                     RETURNING id""",
            o=org_id, c=case_id, tid=to_event_id, st=cit["source_type"], did=cit.get("document_id"),
            pn=cit.get("page_number"), folio=cit.get("folio"), mid=cit.get("media_id"),
            sid=cit.get("segment_id"), sms=cit.get("start_ms"), ems=cit.get("end_ms"), qh=cit.get("quote_hash"))
        n += 1
    return n


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
    # Cuando un referenciado se resuelve a un evento real, el real absorbe sus
    # citas y el referenciado se elimina (evita duplicados y fechas inconsistentes).
    absorbed: list[str] = []
    deleted_refs: list[str] = []
    refers_to_count = 0
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
            # El real absorbe las citas y el referenciado se borra: NO se crea una
            # relación (apuntaría a un evento ya eliminado).
            _absorb_event_citations(conn, org_id, case_id, str(r["id"]), str(best["id"]))
            conn.execute(text("DELETE FROM events WHERE id = :i AND case_id = :c"),
                         {"i": str(r["id"]), "c": case_id})
            absorbed.append(str(best["id"]))
            deleted_refs.append(str(r["id"]))
            refers_to_count += 1

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
                (organization_id, case_id, source_event_id, target_event_id, relationship, provenance, confidence)
            VALUES (:o, :c, :s, :t, :r, 'INFERRED', :conf) ON CONFLICT DO NOTHING"""),
            {"o": org_id, "c": case_id, "s": s, "t": t, "r": r, "conf": conf})
        n += 1
    return {"events": len(evs), "relationships": n,
            "refers_to": refers_to_count,
            "precede": sum(1 for x in rel if x[2] == "precede"),
            "appeals": sum(1 for x in rel if x[2] == "appeals"),
            "responds_to": sum(1 for x in rel if x[2] == "responds_to")}


def review_events(conn: Connection, org_id: str, case_id: str) -> dict[str, Any]:
    evs = rows(conn, """SELECT id, event_date, event_type, subtype, date_type, description, duplicate_of,
                               document_id, page_number
                        FROM events WHERE case_id = :c AND kind = 'procedural'""", c=case_id)
    conn.execute(text("UPDATE events SET review_flags = '[]'::jsonb WHERE case_id = :c AND kind = 'procedural'"),
                 {"c": case_id})

    buckets: dict[tuple, list] = defaultdict(list)
    for e in evs:
        if e["date_type"] == "actuacion" and e["subtype"]:
            buckets[(e["subtype"], str(e["event_date"]))].append(e)

    flags: dict[str, list[str]] = defaultdict(list)
    for group in buckets.values():
        if len(group) <= 1:
            continue
        # Dentro de un mismo bucket, solo marcamos duplicados si las descripciones
        # son muy similares o si provienen exactamente de la misma página del mismo
        # documento. Un auto puede contener varias notificaciones distintas el mismo día.
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                same_doc_page = (a["document_id"] and a["document_id"] == b["document_id"]
                                 and a["page_number"] and a["page_number"] == b["page_number"])
                sim = _sim(a["description"], b["description"])
                if same_doc_page or sim >= 0.82:
                    flags[str(a["id"])].append("duplicado")
                    flags[str(b["id"])].append("duplicado")

    for e in evs:
        if e["date_type"] == "referenciada":
            if not e["duplicate_of"]:
                flags[str(e["id"])].append("sin_fuente_real")
            # Nota: ya no marcamos "fecha_inconsistente". Si un referenciado tiene fecha
            # distinta al real, se resuelve como duplicado en link_events y se borra.
        elif not e["event_date"]:
            flags[str(e["id"])].append("sin_fecha")

    for eid, f in flags.items():
        conn.execute(text("UPDATE events SET review_flags = CAST(:f AS jsonb) WHERE id = :i"),
                     {"f": json.dumps(sorted(set(f))), "i": eid})

    return {"events": len(evs), "flagged": len(flags),
            "duplicado": sum(1 for f in flags.values() if "duplicado" in f),
            "sin_fuente_real": sum(1 for f in flags.values() if "sin_fuente_real" in f),
            "sin_fecha": sum(1 for f in flags.values() if "sin_fecha" in f)}


_ALLOWED_LINK = {"causes", "responds_to", "appeals", "confirms", "revokes", "precede"}
# El LLM a veces usa la forma en inglés; la normalizamos a la canónica (coincide con las reglas).
_REL_ALIAS = {"precedes": "precede"}


def propose_relations_llm(conn: Connection, org_id: str, case_id: str, actor_id: str) -> dict[str, Any]:
    """Segundo pase: el LLM (modelo de la org, p. ej. Gemini) propone relaciones que las reglas no cubren."""
    import hashlib

    from app.core.config import get_settings
    from app.services import legal_extraction as le

    s = get_settings()
    system, prompt_id, prompt_version = le._load_prompt("link_procedural_events", s.DEFAULT_LOCALE)
    evs = rows(conn, """SELECT id, event_date, subtype, instance, actor, description
                        FROM events WHERE case_id = :c AND kind = 'procedural'
                        ORDER BY event_date NULLS LAST LIMIT 1200""", c=case_id)
    if len(evs) < 2:
        return {"links": 0, "model_run": None, "warning": "not_enough_events"}

    schema_path = s.path("packages/schemas/procedural_links.schema.json")
    chunk_size = 60  # por lotes: evita respuestas truncadas por max_tokens
    total = 0
    last_validation: dict[str, Any] | None = None
    for i in range(0, len(evs), chunk_size):
        chunk = evs[i:i + chunk_size]
        if len(chunk) < 2:
            continue
        lines = [f"[{e['id']}] {e['event_date'] or '?'} | {e['subtype']} | {e['instance']} | {e['actor']} | "
                 f"{(e['description'] or '')[:110]}" for e in chunk]
        user = "\n".join(lines)
        input_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()
        result = le._call_llm(conn, case_id, system, user, "link_procedural_events")
        data, validation = le._validate_and_parse(result, schema_path, conn, case_id, actor_id, ["links"])
        le._record_model_run(conn, org_id, case_id, "link_procedural_events", prompt_id, prompt_version,
                             input_hash, result, validation, actor_id)
        last_validation = validation
        if not validation.get("schema_valid"):
            continue
        ids = {str(e["id"]) for e in chunk}
        for link in (data.get("links") or []):
            src, tgt = str(link.get("source_event_id")), str(link.get("target_event_id"))
            rel = _REL_ALIAS.get(link.get("relationship"), link.get("relationship"))
            if src in ids and tgt in ids and src != tgt and rel in _ALLOWED_LINK:
                conn.execute(text("""INSERT INTO event_relationships
                        (organization_id, case_id, source_event_id, target_event_id, relationship, provenance, confidence)
                    VALUES (:o, :c, :s, :t, :r, 'INFERRED', :conf) ON CONFLICT DO NOTHING"""),
                    {"o": org_id, "c": case_id, "s": src, "t": tgt, "r": rel, "conf": link.get("confidence")})
                total += 1
    return {"links": total, "model_run": last_validation}
