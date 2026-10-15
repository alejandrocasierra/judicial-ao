"""Consolidación de actuaciones procesales: fusiona eventos reales duplicados.

El LLM extrae la misma actuación más de una vez (una por documento o fragmento), lo
que deja filas repetidas en la línea de tiempo. Este módulo agrupa los eventos reales
(``date_type = 'actuacion'``) por subtipo + instancia, detecta los que describen el
MISMO acto (misma página del mismo documento, o fecha cercana con texto muy similar)
y los fusiona en uno canónico: re-apunta las citas, une los participantes y borra las
copias. Así la línea de tiempo y el Process Graph no muestran duplicados.

Se ejecuta ANTES de ``link_events`` (handler ``procedural_links``, tras
``legal_extraction`` y en el endpoint «Causas con IA» del dashboard).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import rows

log = logging.getLogger(__name__)

# Umbrales conservadores: preferimos dejar dos eventos separados antes que fusionar
# actuaciones distintas que comparten subtipo y fecha.
NEAR_DAYS = 3
SIM_SAME_DAY = 0.62   # mismo día: descripciones claramente similares
SIM_NEAR_DAYS = 0.80  # ±3 días: exigimos casi el mismo texto
SIM_NO_DATE = 0.88    # sin fecha: solo si el texto es casi idéntico


def _sim(a: str | None, b: str | None) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """¿``a`` y ``b`` describen la misma actuación procesal?"""
    # Misma página del mismo documento: el mismo acto extraído dos veces.
    if (a.get("document_id") and a.get("document_id") == b.get("document_id")
            and a.get("page_number") and a.get("page_number") == b.get("page_number")):
        return True
    ad, bd = a.get("event_date"), b.get("event_date")
    sim = _sim(a.get("description"), b.get("description"))
    if ad and bd:
        days = abs((ad - bd).days)
        if days == 0:
            return sim >= SIM_SAME_DAY
        if days <= NEAR_DAYS:
            return sim >= SIM_NEAR_DAYS
        return False
    if ad is None and bd is None:
        return sim >= SIM_NO_DATE
    # Uno con fecha y otro sin fecha: solo si el texto es casi idéntico.
    return sim >= SIM_NO_DATE


def _merge_cluster(conn: Connection, case_id: str, canonical: dict[str, Any],
                   dupes: list[dict[str, Any]]) -> None:
    """Fusiona ``dupes`` en ``canonical``: citas, participantes y efecto procesal."""
    can_id = str(canonical["id"])
    dup_ids = [str(d["id"]) for d in dupes]

    # Re-apunta las citas de las copias a la canónica (citations.target_id no tiene FK).
    conn.execute(text("""UPDATE citations SET target_id = :can
                         WHERE case_id = :c AND target_type = 'event' AND target_id = ANY(:ids)"""),
                 {"can": can_id, "c": case_id, "ids": dup_ids})

    # Une participantes (sin perder a nadie de las copias).
    parts: set[str] = {str(p) for p in (canonical.get("participants") or [])}
    for d in dupes:
        parts.update(str(p) for p in (d.get("participants") or []))

    # Une los efectos procesales que aporten información nueva.
    effects: list[str] = []
    if canonical.get("procedural_effect"):
        effects.append(str(canonical["procedural_effect"]))
    for d in dupes:
        eff = d.get("procedural_effect")
        if eff and str(eff) not in effects:
            effects.append(str(eff))

    # Limpia autoreferencias antes de borrar (events.duplicate_of es FK a events).
    conn.execute(text("UPDATE events SET duplicate_of = NULL WHERE case_id = :c AND duplicate_of = ANY(:ids)"),
                 {"c": case_id, "ids": dup_ids})

    # Borra las copias; event_relationships cae por ON DELETE CASCADE y se reconstruye.
    conn.execute(text("DELETE FROM events WHERE id = ANY(:ids) AND case_id = :c"),
                 {"ids": dup_ids, "c": case_id})

    conn.execute(text("""UPDATE events
                         SET participants = CAST(:p AS uuid[]),
                             procedural_effect = :eff
                         WHERE id = :i AND case_id = :c"""),
                 {"p": sorted(parts), "eff": " ".join(effects) if effects else None,
                  "i": can_id, "c": case_id})


def consolidate_case_events(conn: Connection, org_id: str, case_id: str) -> dict[str, Any]:
    """Fusiona las actuaciones reales duplicadas del caso. Devuelve estadísticas."""
    evs = rows(conn, """
        SELECT id, event_date, subtype, instance, authority, actor, description,
               document_id, page_number, participants, procedural_effect
        FROM events
        WHERE case_id = :c AND kind = 'procedural' AND date_type = 'actuacion'
        ORDER BY event_date NULLS LAST
    """, c=case_id)

    buckets: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for e in evs:
        if e.get("subtype"):
            buckets[(e["subtype"], e.get("instance"))].append(e)

    merged = deleted = 0
    for group in buckets.values():
        if len(group) < 2:
            continue
        used: set[str] = set()
        for i, a in enumerate(group):
            if str(a["id"]) in used:
                continue
            cluster = [a]
            for b in group[i + 1:]:
                if str(b["id"]) in used:
                    continue
                if _same_event(a, b):
                    cluster.append(b)
            if len(cluster) < 2:
                continue
            _merge_cluster(conn, case_id, cluster[0], cluster[1:])
            used.update(str(x["id"]) for x in cluster)
            merged += 1
            deleted += len(cluster) - 1

    if merged:
        log.info("consolidación eventos caso %s: fusionados=%s borrados=%s", case_id, merged, deleted)
    return {"merged": merged, "deleted": deleted, "events": len(evs)}


def consolidate_all_cases(conn: Connection) -> dict[str, Any]:
    """Consolida eventos de TODOS los casos (barrido global). Devuelve totales."""
    orgs = rows(conn, "SELECT DISTINCT organization_id, case_id FROM events")
    merged = deleted = 0
    for r in orgs:
        res = consolidate_case_events(conn, str(r["organization_id"]), str(r["case_id"]))
        merged += res["merged"]
        deleted += res["deleted"]
    return {"cases": len(orgs), "merged": merged, "deleted": deleted}
