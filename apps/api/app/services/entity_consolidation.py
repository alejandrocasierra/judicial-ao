"""Consolidación de entidades: deduplica por nombre normalizado.

El LLM extrae la misma entidad muchas veces (una por fragmento), a veces con tipos
distintos, y `_insert_entities` inserta por `id` (el modelo inventa ids) => filas
duplicadas. Esta consolidación deja **UNA entidad canónica por nombre normalizado**
eligiendo el **tipo dominante**, fusiona los nombres alternos como **alias**,
**re-apunta las citas** a la canónica y **borra las duplicadas**.

Se ejecuta automáticamente antes de reconstruir el grafo (handler `graph_build`), así
que cualquier subida (OCR/ASR → legal_extraction → graph_build) deja la BD limpia.
"""
from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import rows

log = logging.getLogger(__name__)


def consolidate_case_entities(conn: Connection, org_id: str, case_id: str) -> dict:
    """Deduplica las entidades del caso por `normalized_name`. Devuelve estadísticas."""
    groups = rows(conn, """
        SELECT normalized_name FROM entities
        WHERE case_id = :c AND coalesce(normalized_name, '') <> ''
        GROUP BY normalized_name HAVING count(*) > 1
    """, c=case_id)
    merged = deleted = 0
    for g in groups:
        nn = g["normalized_name"]
        ents = rows(conn, "SELECT id, entity_type, name, aliases FROM entities "
                          "WHERE case_id = :c AND normalized_name = :nn", c=case_id, nn=nn)
        if len(ents) < 2:
            continue
        # Tipo dominante (por número de filas) -> entidad canónica de ese tipo.
        dominant = Counter(e["entity_type"] for e in ents).most_common(1)[0][0]
        canonical = next((e for e in ents if e["entity_type"] == dominant), ents[0])
        dup_ids = [str(e["id"]) for e in ents if str(e["id"]) != str(canonical["id"])]
        if not dup_ids:
            continue
        # Alias = nombres alternos (los de las duplicadas) + alias previos, sin el canónico.
        aliases: set[str] = set()
        for e in ents:
            for a in (e["aliases"] or []):
                if a and a != canonical["name"]:
                    aliases.add(a)
            if e["name"] and e["name"] != canonical["name"]:
                aliases.add(e["name"])
        # Re-apunta las citas de las duplicadas a la canónica (citations no tiene FK).
        conn.execute(text("UPDATE citations SET target_id = :can "
                          "WHERE case_id = :c AND target_type = 'entity' AND target_id = ANY(:ids)"),
                     {"can": str(canonical["id"]), "c": case_id, "ids": dup_ids})
        # Borra las duplicadas y guarda los alias en la canónica.
        deleted += conn.execute(text("DELETE FROM entities WHERE id = ANY(:ids) AND case_id = :c"),
                                {"ids": dup_ids, "c": case_id}).rowcount
        conn.execute(text("UPDATE entities SET aliases = :a WHERE id = :i"),
                     {"a": sorted(aliases), "i": str(canonical["id"])})
        merged += 1
    if merged:
        log.info("consolidación entidades caso %s: grupos=%s fusionados=%s borradas=%s",
                 case_id, len(groups), merged, deleted)
    return {"groups": len(groups), "merged": merged, "deleted": deleted}


def consolidate_all_cases(conn: Connection) -> dict:
    """Consolida entidades de TODOS los casos (barrido global). Devuelve totales."""
    orgs = rows(conn, "SELECT DISTINCT organization_id, case_id FROM entities")
    merged = deleted = 0
    for r in orgs:
        res = consolidate_case_entities(conn, str(r["organization_id"]), str(r["case_id"]))
        merged += res["merged"]
        deleted += res["deleted"]
    return {"cases": len(orgs), "merged": merged, "deleted": deleted}
