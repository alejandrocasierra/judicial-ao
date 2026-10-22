"""Ledger de hechos por parte y estado de prueba.

Regla dura:
- `JUDICIALLY_PROVEN` exige decision judicial con documento fuente.
- `ADMITTED` exige una posicion de parte `ADMITS`.
- `EVIDENCE_SUPPORTED` es prueba disponible, NO hecho probado.
- La UI solo muestra como probados `ADMITTED` y `JUDICIALLY_PROVEN`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows
from app.services import text_terms

log = logging.getLogger(__name__)

PROOF_STATUSES = {
    "ALLEGED", "ADMITTED", "EVIDENCE_SUPPORTED", "JUDICIALLY_PROVEN",
    "DISPUTED", "REJECTED", "UNRESOLVED",
}
PROVEN_STATUSES = {"ADMITTED", "JUDICIALLY_PROVEN"}


class ProofStatusDisabled(RuntimeError):
    """La transición a un estado probatorio está apagada por FACTS_PROOF_ENABLED."""


def proof_transitions_allowed() -> bool:
    """Fail closed: sin configuración legible no se promueve a probado."""
    try:
        return bool(get_settings().FACTS_PROOF_ENABLED)
    except Exception:  # noqa: BLE001 — configuración rota no habilita prueba
        log.warning("FACTS_PROOF_ENABLED unavailable; blocking proof transitions")
        return False


def _assert_proof_transition_allowed(fact: dict[str, Any], requested: str | None) -> None:
    if (requested in PROVEN_STATUSES
            and str(fact.get("proof_status") or "") != str(requested)
            and not proof_transitions_allowed()):
        raise ProofStatusDisabled("facts_proof_disabled")
POSITIONS = {"ALLEGES", "DENIES", "ADMITS", "UNKNOWN"}
SIDES = {"DEMANDANTE", "DEMANDADO", "TERCERO", "NO_ATRIBUIDO"}
ROLE_SIDE = {
    "claimant": "DEMANDANTE", "plaintiff": "DEMANDANTE", "ejecutante": "DEMANDANTE",
    "defendant": "DEMANDADO", "respondent": "DEMANDADO", "ejecutado": "DEMANDADO",
}


def side_for_role(role: str | None) -> str:
    return ROLE_SIDE.get((role or "").lower(), "TERCERO")


def _positions(conn: Connection, fact_id: str) -> list[dict[str, Any]]:
    return rows(conn, """
        SELECT fp.id, fp.party_id, p.name AS party_name, p.role AS party_role,
               fp.side, fp.position, fp.source_claim_id, fp.source_citation_id,
               fp.confidence, fp.created_at, fp.updated_at
        FROM fact_positions fp
        LEFT JOIN parties p ON p.id = fp.party_id
        WHERE fp.fact_id = :f
        ORDER BY CASE fp.side WHEN 'DEMANDANTE' THEN 1 WHEN 'DEMANDADO' THEN 2 ELSE 3 END,
                 p.name NULLS LAST
    """, f=fact_id)


def _evidence(conn: Connection, fact_id: str) -> list[dict[str, Any]]:
    return rows(conn, """
        SELECT el.id, el.evidence_id, e.evidence_type, e.description,
               e.admissibility_status, e.source_document_id, e.source_media_id,
               el.stance, el.citation_id
        FROM evidence_links el JOIN evidence e ON e.id = el.evidence_id
        WHERE el.fact_id = :f ORDER BY el.id
    """, f=fact_id)


def _citations(conn: Connection, fact_id: str) -> list[dict[str, Any]]:
    return rows(conn, """
        SELECT c.id, c.source_type, c.document_id, c.page_number, c.folio,
               c.media_id, c.segment_id, c.start_ms, c.end_ms, c.quote_hash,
               d.filename
        FROM citations c LEFT JOIN documents d ON d.id = c.document_id
        WHERE c.target_type = 'fact' AND c.target_id = :f
        ORDER BY c.created_at
    """, f=fact_id)


def _revision_snapshot(conn: Connection, fact: dict[str, Any]) -> dict[str, Any]:
    positions = _positions(conn, str(fact["id"]))
    evidence = _evidence(conn, str(fact["id"]))
    citations = _citations(conn, str(fact["id"]))
    return {
        "positions": positions,
        "evidence_ids": [str(e["evidence_id"]) for e in evidence],
        "citations": citations,
    }


def _revision(conn: Connection, fact: dict[str, Any], *, actor_id: str | None, reason: str) -> None:
    version = int(fact.get("version") or 1)
    next_version = version + 1
    snapshot = _revision_snapshot(conn, fact)
    conn.execute(text("""
        INSERT INTO fact_revisions
          (organization_id, case_id, fact_id, version, proposition, proof_status,
           proof_basis, proven_by_decision_id, positions, evidence_ids, citations,
           changed_by, reason)
        VALUES
          (:o, :c, :f, :v, :p, :ps, :pb, :dec, CAST(:pos AS jsonb),
           CAST(:ev AS uuid[]), CAST(:cit AS jsonb), :by, :reason)
        ON CONFLICT (fact_id, version) DO NOTHING
    """), {"o": str(fact["organization_id"]), "c": str(fact["case_id"]), "f": str(fact["id"]),
           "v": next_version, "p": fact["proposition"], "ps": fact.get("proof_status") or "ALLEGED",
           "pb": fact.get("proof_basis") or "NONE", "dec": fact.get("proven_by_decision_id"),
           "pos": json.dumps(snapshot["positions"], ensure_ascii=False, default=str),
           "ev": "{" + ",".join(snapshot["evidence_ids"]) + "}",
           "cit": json.dumps(snapshot["citations"], ensure_ascii=False, default=str),
           "by": actor_id, "reason": reason[:1000]})
    conn.execute(text("UPDATE facts SET version = :v WHERE id = :f"), {"v": next_version, "f": str(fact["id"])})


def _derive_proof(conn: Connection, fact: dict[str, Any]) -> tuple[str, str, str | None]:
    """Devuelve (proof_status, proof_basis, decision_id) para un hecho."""
    status = str(fact.get("status") or "ALLEGED")
    positions = _positions(conn, str(fact["id"]))
    evidence = _evidence(conn, str(fact["id"]))
    decision_id = fact.get("proven_by_decision_id") or fact.get("determined_by_decision_id")

    if decision_id:
        dec = one(conn, "SELECT id, source_document_id FROM decisions WHERE id = :d AND case_id = :c",
                  d=str(decision_id), c=str(fact["case_id"]))
        if dec and dec.get("source_document_id"):
            return "JUDICIALLY_PROVEN", "JUDICIAL_DECISION", str(dec["id"])
    if status == "JUDICIALLY_DETERMINED" and decision_id:
        dec = one(conn, "SELECT id, source_document_id FROM decisions WHERE id = :d AND case_id = :c",
                  d=str(decision_id), c=str(fact["case_id"]))
        if dec and dec.get("source_document_id"):
            return "JUDICIALLY_PROVEN", "JUDICIAL_DECISION", str(dec["id"])
    if any(p.get("position") == "ADMITS" for p in positions):
        return "ADMITTED", "ADMISSION", None
    supports = [e for e in evidence if e.get("stance") == "supports" and e.get("admissibility_status") != "rejected"]
    refutes = [e for e in evidence if e.get("stance") == "refutes" and e.get("admissibility_status") != "rejected"]
    if supports and refutes:
        return "DISPUTED", "EVIDENCE", None
    if status in {"CONTRADICTED", "DISPUTED"}:
        return "DISPUTED", "EVIDENCE", None
    if supports:
        return "EVIDENCE_SUPPORTED", "EVIDENCE", None
    if status == "REJECTED":
        return "REJECTED", "NONE", None
    if status == "UNRESOLVED":
        return "UNRESOLVED", "NONE", None
    return "ALLEGED", "NONE", None


def _fact_row(conn: Connection, case_id: str, fact_id: str) -> dict[str, Any] | None:
    return one(conn, "SELECT * FROM facts WHERE id = :f AND case_id = :c", f=fact_id, c=case_id)


def _claims(conn: Connection, fact_id: str) -> list[dict[str, Any]]:
    """Claims vinculados y su postura; compatibilidad con el contrato legado."""
    return rows(conn, """
        SELECT cl.id, cl.text, fc.stance, cl.claim_type,
               p.name AS claimant, cl.confidence, fc.claim_id
        FROM fact_claims fc
        JOIN claims cl ON cl.id = fc.claim_id
        LEFT JOIN parties p ON p.id = cl.claimant_party_id
        WHERE fc.fact_id = :f
        ORDER BY fc.stance, cl.created_at
    """, f=fact_id)


def _serialize(conn: Connection, fact: dict[str, Any]) -> dict[str, Any]:
    out = dict(fact)
    out["positions"] = _positions(conn, str(fact["id"]))
    out["claims"] = _claims(conn, str(fact["id"]))
    out["evidence"] = _evidence(conn, str(fact["id"]))
    out["citations"] = _citations(conn, str(fact["id"]))
    if fact.get("proven_by_decision_id") or fact.get("determined_by_decision_id"):
        out["decision"] = one(conn, """
            SELECT d.id, d.decision_type, d.decision_date, d.outcome, d.reasoning,
                   d.source_document_id, doc.filename
            FROM decisions d LEFT JOIN documents doc ON doc.id = d.source_document_id
            WHERE d.id = :d
        """, d=str(fact.get("proven_by_decision_id") or fact.get("determined_by_decision_id")))
    else:
        out["decision"] = None
    out["is_proven"] = bool(fact.get("is_proven"))
    return out


def list_facts(conn: Connection, case_id: str, *, proof: str = "proven",
               side: str | None = None, party_id: str | None = None,
               issue_id: str | None = None, q: str | None = None,
               limit: int = 200) -> list[dict[str, Any]]:
    where = ["f.case_id = :c"]
    params: dict[str, Any] = {"c": case_id, "limit": max(1, min(int(limit or 200), 500))}
    if proof == "proven":
        where.append("f.is_proven")
    elif proof in {"supported", "evidence_supported"}:
        where.append("f.proof_status = 'EVIDENCE_SUPPORTED'")
    elif proof == "disputed":
        where.append("f.proof_status = 'DISPUTED'")
    elif proof != "all":
        where.append("f.proof_status = :ps")
        params["ps"] = proof.upper()
    if side:
        where.append("EXISTS (SELECT 1 FROM fact_positions fp WHERE fp.fact_id=f.id AND fp.side=:side)")
        params["side"] = side.upper()
    if party_id:
        where.append("EXISTS (SELECT 1 FROM fact_positions fp WHERE fp.fact_id=f.id AND fp.party_id=:pid)")
        params["pid"] = party_id
    if issue_id:
        where.append("f.issue_id = :iid")
        params["iid"] = issue_id
    if q:
        where.append("(f.proposition ILIKE :q OR coalesce(f.fact_code,'') ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    sql = "SELECT f.* FROM facts f"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += """
        ORDER BY f.is_proven DESC, f.updated_at DESC, f.id
        LIMIT :limit
    """
    facts = rows(conn, sql, **params)
    return [_serialize(conn, f) for f in facts]


def get_fact(conn: Connection, case_id: str, fact_id: str, *, with_revisions: bool = False) -> dict[str, Any] | None:
    fact = _fact_row(conn, case_id, fact_id)
    if not fact:
        return None
    out = _serialize(conn, fact)
    if with_revisions:
        out["revisions"] = rows(conn, """
            SELECT id, version, proposition, proof_status, proof_basis,
                   proven_by_decision_id, positions, evidence_ids, citations,
                   changed_by, reason, created_at
            FROM fact_revisions WHERE fact_id = :f ORDER BY version DESC
        """, f=fact_id)
    return out


def ensure_positions_from_claims(conn: Connection, org_id: str, case_id: str,
                                 fact_id: str, actor_id: str | None = None) -> int:
    """Crea posiciones faltantes desde fact_claims/claims; no inventa admisiones."""
    result = conn.execute(text("""
        INSERT INTO fact_positions
          (organization_id, case_id, fact_id, party_id, side, position,
           source_claim_id, source_citation_id, confidence, created_by)
        SELECT :o, :c, fc.fact_id, cl.claimant_party_id,
               CASE
                 WHEN p.role IN ('claimant','plaintiff','ejecutante') THEN 'DEMANDANTE'
                 WHEN p.role IN ('defendant','respondent','ejecutado') THEN 'DEMANDADO'
                 WHEN cl.claimant_party_id IS NULL THEN 'NO_ATRIBUIDO'
                 ELSE 'TERCERO'
               END,
               CASE fc.stance WHEN 'asserts' THEN 'ALLEGES' ELSE 'DENIES' END,
               cl.id,
               (SELECT ci.id FROM citations ci WHERE ci.target_type='claim' AND ci.target_id=cl.id
                 ORDER BY ci.created_at LIMIT 1),
               cl.confidence, :u
        FROM fact_claims fc
        JOIN claims cl ON cl.id = fc.claim_id AND cl.case_id = :c
        LEFT JOIN parties p ON p.id = cl.claimant_party_id AND p.case_id = :c
        WHERE fc.fact_id = :f
          AND NOT EXISTS (
            SELECT 1 FROM fact_positions fp WHERE fp.fact_id = fc.fact_id
              AND fp.party_id IS NOT DISTINCT FROM cl.claimant_party_id
              AND fp.position = CASE fc.stance WHEN 'asserts' THEN 'ALLEGES' ELSE 'DENIES' END
          )
    """), {"o": org_id, "c": case_id, "f": fact_id, "u": actor_id})
    return int(result.rowcount or 0)


_FIRST_PERSON_ADMISSION = re.compile(
    r"\b(?:admito|admitimos|acepto|aceptamos|reconozco|reconocemos|confieso|confesamos|"
    r"me\s+allano|nos\s+allanamos)\b", re.I)
_THIRD_PERSON_ADMISSION = re.compile(
    r"\b(?:admite|admiten|acepta|aceptan|reconoce|reconocen|confiesa|confiesan|"
    r"se\s+allana|se\s+allanan)\b", re.I)
_FIRST_PERSON_DENIAL = re.compile(
    r"\b(?:niego|negamos|rechazo|rechazamos|me\s+opongo|nos\s+oponemos|"
    r"no\s+acepto|no\s+aceptamos|no\s+reconozco|no\s+reconocemos|no\s+admito|no\s+admitimos)\b", re.I)
_THIRD_PERSON_DENIAL = re.compile(
    r"\b(?:niega|niegan|rechaza|rechazan|se\s+opone|se\s+oponen|"
    r"no\s+acepta|no\s+aceptan|no\s+reconoce|no\s+reconocen|no\s+admite|no\s+admiten)\b", re.I)
_FIRST_PERSON_ALLEGATION = re.compile(
    r"\b(?:alego|alegamos|sostengo|sostenemos|afirmo|afirmamos|manifiesto|manifestamos|"
    r"argumento|argumentamos|solicito|solicitamos)\b", re.I)
_THIRD_PERSON_ALLEGATION = re.compile(
    r"\b(?:alega|alegan|sostiene|sostienen|afirma|afirman|manifiesta|manifiestan|"
    r"argumenta|argumentan)\b", re.I)


def infer_stance(text: str, *, party_name: str | None, speaker_party_id: str | None,
                 target_party_id: str | None) -> str:
    """Extrae ALLEGES/DENIES/ADMITS solo de una declaración atribuible a la parte.

    Reglas duras:
    - sin hablante conocido no hay posición;
    - una afirmación de A sobre lo que dijo B no crea posición de B;
    - ADMITS exige verbo explícito de admisión en primera persona o auto-referencia
      de la propia parte. "menciona", "consta" o "figura" no son admisión;
    - "no niego" no se convierte en admisión.
    """
    folded = text_terms.norm(text)
    if not folded or not speaker_party_id:
        return "UNKNOWN"
    if target_party_id and str(speaker_party_id) != str(target_party_id):
        return "UNKNOWN"
    if re.search(r"\bno\s+nieg[oa]\b", folded):
        return "UNKNOWN"
    name = text_terms.norm(party_name or "")
    self_reference = bool(name and name in folded)
    first_person = bool(re.search(r"\b(?:yo|nosotros|mi\s+representado)\b", folded))
    denial_at_start = bool(re.match(
        r"^\s*(?:niega|niegan|rechaza|rechazan|se\s+opone|se\s+oponen|"
        r"no\s+acepta|no\s+aceptan|no\s+reconoce|no\s+reconocen|no\s+admite|no\s+admiten)\b",
        folded))
    admission_at_start = bool(re.match(
        r"^\s*(?:admite|admiten|acepta|aceptan|reconoce|reconocen|confiesa|confiesan|"
        r"se\s+allana|se\s+allanan)\b", folded))
    allegation_at_start = bool(re.match(
        r"^\s*(?:alega|alegan|sostiene|sostienen|afirma|afirman|manifiesta|manifiestan|"
        r"argumenta|argumentan)\b", folded))

    if (_FIRST_PERSON_DENIAL.search(folded)
            or (self_reference and _THIRD_PERSON_DENIAL.search(folded))
            or denial_at_start):
        return "DENIES"
    if _FIRST_PERSON_ADMISSION.search(folded):
        return "ADMITS"
    if ((self_reference or admission_at_start) and _THIRD_PERSON_ADMISSION.search(folded)
            and not _THIRD_PERSON_DENIAL.search(folded)):
        return "ADMITS"
    if (_FIRST_PERSON_ALLEGATION.search(folded)
            or (self_reference and _THIRD_PERSON_ALLEGATION.search(folded))
            or allegation_at_start):
        return "ALLEGES"
    if first_person and self_reference:
        # Declaración en primera persona sin verbo de postura: no se inventa.
        return "UNKNOWN"
    return "UNKNOWN"


def extract_positions_from_claims(conn: Connection, org_id: str, case_id: str,
                                  actor_id: str | None = None, *, dry_run: bool = True,
                                  limit: int = 5000) -> dict[str, Any]:
    """Convierte claims explícitos en posiciones con cita; nunca toca UNKNOWN a ciegas.

    Solo actualiza posiciones `UNKNOWN` o crea la faltante. Una posición revisada
    por un humano (ALLEGES/DENIES/ADMITS) no se sobrescribe. No cambia el estado
    de prueba ni `is_proven`; eso lo decide `_derive_proof` y la revisión humana.
    """
    linked = rows(conn, """
        SELECT fc.fact_id, cl.id AS claim_id, cl.text, cl.claim_type,
               cl.claimant_party_id, cl.confidence,
               sp.resolved_party_id AS speaker_party_id,
               p.name AS party_name, p.role AS party_role,
               'linked' AS source
        FROM fact_claims fc
        JOIN claims cl ON cl.id = fc.claim_id AND cl.case_id = :c
        LEFT JOIN LATERAL (
          SELECT s2.resolved_party_id
          FROM citations ci
          JOIN transcript_segments ts ON ts.id = ci.segment_id
          JOIN speakers s2 ON s2.id = ts.speaker_id
          WHERE ci.target_type='claim' AND ci.target_id=cl.id
            AND s2.resolved_party_id IS NOT NULL
          LIMIT 1
        ) sp ON true
        LEFT JOIN parties p ON p.id = coalesce(cl.claimant_party_id, sp.resolved_party_id)
        WHERE cl.review_status <> 'REJECTED'
        ORDER BY fc.fact_id, cl.created_at
        LIMIT :n
    """, c=case_id, n=max(1, min(int(limit), 10000)))
    # Sin fact_claims, solo se usa una coincidencia EXACTA de proposición y un
    # hablante resuelto a una parte. No se infiere por parecido ni por mención.
    unlinked = rows(conn, """
        SELECT f.id AS fact_id, cl.id AS claim_id, cl.text, cl.claim_type,
               cl.claimant_party_id, cl.confidence,
               sp.resolved_party_id AS speaker_party_id,
               p.name AS party_name, p.role AS party_role,
               'exact_proposition' AS source
        FROM facts f
        JOIN claims cl ON cl.case_id = f.case_id
          AND lower(btrim(cl.text)) = lower(btrim(f.proposition))
        LEFT JOIN LATERAL (
          SELECT s2.resolved_party_id
          FROM citations ci
          JOIN transcript_segments ts ON ts.id = ci.segment_id
          JOIN speakers s2 ON s2.id = ts.speaker_id
          WHERE ci.target_type='claim' AND ci.target_id=cl.id
            AND s2.resolved_party_id IS NOT NULL
          LIMIT 1
        ) sp ON true
        LEFT JOIN parties p ON p.id = coalesce(cl.claimant_party_id, sp.resolved_party_id)
        WHERE f.case_id = :c
          AND cl.review_status <> 'REJECTED'
          AND NOT EXISTS (SELECT 1 FROM fact_claims fc WHERE fc.fact_id = f.id)
        ORDER BY f.id, cl.created_at
        LIMIT :n
    """, c=case_id, n=max(1, min(int(limit), 10000)))
    claims = linked + unlinked
    stats = {"claims": len(claims), "linked": len(linked), "exact_proposition": len(unlinked),
             "inserted": 0, "updated": 0, "unchanged": 0,
             "unknown": 0, "conflicts": 0, "dry_run": dry_run, "changes": []}
    for claim in claims:
        fact_id = str(claim["fact_id"])
        party_id = claim.get("claimant_party_id") or claim.get("speaker_party_id")
        position = infer_stance(str(claim.get("text") or ""),
                                party_name=claim.get("party_name"),
                                speaker_party_id=str(party_id) if party_id else None,
                                target_party_id=str(party_id) if party_id else None)
        if (position == "UNKNOWN" and party_id
                and str(claim.get("claim_type") or "") == "party_assertion"):
            # Una afirmación de la parte sobre el hecho, con hablante resuelto,
            # es una alegación: ALLEGES. Nunca se eleva a ADMITS sin verbo.
            position = "ALLEGES"
        if position == "UNKNOWN":
            stats["unknown"] += 1
            continue
        existing = one(conn, """
            SELECT id, position FROM fact_positions
            WHERE fact_id=:f AND party_id IS NOT DISTINCT FROM :p
            ORDER BY created_at LIMIT 1
        """, f=fact_id, p=str(party_id) if party_id else None)
        if existing and str(existing.get("position")) not in {"UNKNOWN"}:
            if str(existing.get("position")) != position:
                stats["conflicts"] += 1
            else:
                stats["unchanged"] += 1
            continue
        citation = one(conn, """
            SELECT id FROM citations WHERE target_type='claim' AND target_id=:cl
            ORDER BY created_at LIMIT 1
        """, cl=str(claim["claim_id"]))
        change = {
            "fact_id": fact_id, "claim_id": str(claim["claim_id"]),
            "party_id": str(party_id) if party_id else None,
            "party_name": claim.get("party_name"),
            "from": (existing or {}).get("position") or "MISSING",
            "to": position, "citation_id": str(citation["id"]) if citation else None,
        }
        if existing:
            stats["updated"] += 1
            if not dry_run:
                conn.execute(text("""
                    UPDATE fact_positions
                    SET position=:pos, source_claim_id=:cl, source_citation_id=:ci,
                        confidence=coalesce(confidence, :conf), updated_at=now()
                    WHERE id=:id
                """), {"pos": position, "cl": str(claim["claim_id"]),
                       "ci": change["citation_id"],
                       "conf": claim.get("confidence") if claim.get("confidence") is not None else 0.60,
                       "id": str(existing["id"])})
        else:
            stats["inserted"] += 1
            if not dry_run:
                side = side_for_role(claim.get("party_role")) if party_id else "NO_ATRIBUIDO"
                conn.execute(text("""
                    INSERT INTO fact_positions
                      (organization_id, case_id, fact_id, party_id, side, position,
                       source_claim_id, source_citation_id, confidence, created_by)
                    VALUES (:o,:c,:f,:p,:s,:pos,:cl,:ci,:conf,:u)
                """), {"o": org_id, "c": case_id, "f": fact_id, "p": str(party_id) if party_id else None,
                       "s": side, "pos": position, "cl": str(claim["claim_id"]),
                       "ci": change["citation_id"],
                       "conf": claim.get("confidence") if claim.get("confidence") is not None else 0.60,
                       "u": actor_id})
        if len(stats["changes"]) < 200:
            stats["changes"].append(change)
    return stats


def ensure_positions_from_parties(conn: Connection, org_id: str, case_id: str,
                                  fact: dict[str, Any], actor_id: str | None = None) -> int:
    """Si no hay claims vinculados, atribuye el hecho a las partes nombradas.

    La posicion queda `UNKNOWN` (no se inventa que alega o admite). La UI puede
    mostrar el lado y el revisor la corrige. Es una atribucion de mencion, no una
    prueba.
    """
    if _positions(conn, str(fact["id"])):
        return 0
    proposition = text_terms.norm(str(fact.get("proposition") or ""))
    if not proposition:
        return 0
    created = 0
    citation = one(conn, """SELECT id FROM citations WHERE target_type='fact'
                            AND target_id=:f ORDER BY created_at LIMIT 1""", f=str(fact["id"]))
    for party in rows(conn, "SELECT id, name, role, aliases FROM parties WHERE case_id=:c", c=case_id):
        names = [party.get("name")] + list(party.get("aliases") or [])
        if not any(text_terms.norm(str(n)) and text_terms.norm(str(n)) in proposition for n in names if n):
            continue
        conn.execute(text("""
            INSERT INTO fact_positions
              (organization_id, case_id, fact_id, party_id, side, position,
               source_citation_id, confidence, created_by)
            VALUES (:o,:c,:f,:p,:s,'UNKNOWN',:ci,0.30,:u)
            ON CONFLICT DO NOTHING
        """), {"o": org_id, "c": case_id, "f": str(fact["id"]), "p": str(party["id"]),
               "s": side_for_role(party.get("role")),
               "ci": str(citation["id"]) if citation else None, "u": actor_id})
        created += 1
    return created


def rebuild_case(conn: Connection, org_id: str, case_id: str, actor_id: str | None = None,
                 *, limit: int = 1000) -> dict[str, Any]:
    facts = rows(conn, "SELECT * FROM facts WHERE case_id = :c ORDER BY id LIMIT :n",
                 c=case_id, n=max(1, min(int(limit), 5000)))
    # Primero posiciones explícitas (alega/niega/admite) desde claims con cita.
    # Después las heurísticas existentes, que nunca convierten UNKNOWN en ADMITS.
    extraction = extract_positions_from_claims(conn, org_id, case_id, actor_id,
                                               dry_run=False, limit=max(limit * 8, 1000))
    changed = 0
    revisions = 0
    for fact in facts:
        ensure_positions_from_claims(conn, org_id, case_id, str(fact["id"]), actor_id)
        ensure_positions_from_parties(conn, org_id, case_id, fact, actor_id)
        if fact.get("review_status") in {"ACCEPTED", "EDITED"}:
            continue
        proof, basis, decision_id = _derive_proof(conn, fact)
        if (proof in PROVEN_STATUSES
                and str(fact.get("proof_status") or "") != proof
                and not proof_transitions_allowed()):
            # La lectura y los estados no probatorios siguen funcionando; no se
            # promueve a ADMITTED/JUDICIALLY_PROVEN con el flag apagado.
            continue
        if (proof != fact.get("proof_status") or basis != fact.get("proof_basis")
                or str(decision_id) != str(fact.get("proven_by_decision_id"))):
            conn.execute(text("""
                UPDATE facts SET proof_status=:ps, proof_basis=:pb,
                  proven_by_decision_id=:dec, proven_at=:pa
                WHERE id=:f
            """), {"ps": proof, "pb": basis, "dec": decision_id,
                   "pa": (one(conn, "SELECT now() AS n")["n"] if proof == "JUDICIALLY_PROVEN" else None),
                   "f": str(fact["id"])})
            fact["proof_status"] = proof
            fact["proof_basis"] = basis
            fact["proven_by_decision_id"] = decision_id
            _revision(conn, fact, actor_id=actor_id, reason="Reconciliacion automatica de prueba")
            changed += 1
            revisions += 1
    return {"facts": len(facts), "changed": changed, "revisions": revisions,
            "positions_linked": extraction.get("linked", 0),
            "positions_exact": extraction.get("exact_proposition", 0),
            "positions_inserted": extraction["inserted"],
            "positions_updated": extraction["updated"],
            "positions_unknown": extraction["unknown"],
            "positions_conflicts": extraction["conflicts"]}


def update_fact(conn: Connection, *, org_id: str, case_id: str, fact_id: str,
                expected_version: int, actor_id: str | None, reason: str,
                proof_status: str | None = None, positions: list[dict[str, Any]] | None = None,
                proposition: str | None = None, issue_id: str | None = None,
                fact_code: str | None = None) -> dict[str, Any]:
    fact = _fact_row(conn, case_id, fact_id)
    if not fact:
        raise LookupError("fact_not_found")
    if int(fact.get("version") or 1) != int(expected_version):
        raise ValueError("version_conflict")
    if proof_status is not None and proof_status not in PROOF_STATUSES:
        raise ValueError("invalid_proof_status")
    _assert_proof_transition_allowed(fact, proof_status)
    if positions is not None:
        for pos in positions:
            if pos.get("side") not in SIDES or pos.get("position") not in POSITIONS:
                raise ValueError("invalid_position")
    # Validacion dura antes de escribir.
    if proof_status == "JUDICIALLY_PROVEN":
        dec_id = fact.get("proven_by_decision_id") or fact.get("determined_by_decision_id")
        if not dec_id:
            raise ValueError("judicially_proven_requires_decision")
        dec = one(conn, "SELECT id, source_document_id FROM decisions WHERE id=:d AND case_id=:c",
                  d=str(dec_id), c=case_id)
        if not dec or not dec.get("source_document_id"):
            raise ValueError("judicially_proven_requires_source_document")
    if proof_status == "ADMITTED":
        rows_for_admission = positions if positions is not None else _positions(conn, fact_id)
        if not any(p.get("position") == "ADMITS" for p in rows_for_admission):
            raise ValueError("admitted_requires_party_admission")

    if proposition is not None and proposition.strip():
        fact["proposition"] = proposition.strip()
    if issue_id is not None:
        fact["issue_id"] = issue_id or None
    if fact_code is not None:
        fact["fact_code"] = fact_code.strip() or None
    if proof_status is not None:
        fact["proof_status"] = proof_status
        fact["proof_basis"] = ("ADMISSION" if proof_status == "ADMITTED"
                               else "JUDICIAL_DECISION" if proof_status == "JUDICIALLY_PROVEN"
                               else "EVIDENCE" if proof_status in {"EVIDENCE_SUPPORTED", "DISPUTED"}
                               else "NONE")
    if positions is not None:
        for pos in positions:
            party_id = pos.get("party_id")
            side = pos.get("side") or "NO_ATRIBUIDO"
            existing = one(conn, """
                SELECT id FROM fact_positions WHERE fact_id=:f AND party_id IS NOT DISTINCT FROM :p
                  AND (:p IS NOT NULL OR (side=:s AND position=:pos))
                LIMIT 1
            """, f=fact_id, p=party_id, s=side, pos=pos.get("position"))
            if existing:
                conn.execute(text("""
                    UPDATE fact_positions SET side=:s, position=:pos, source_claim_id=:sc,
                      source_citation_id=:ci, confidence=:conf, updated_at=now()
                    WHERE id=:id
                """), {"s": side, "pos": pos.get("position"), "sc": pos.get("source_claim_id"),
                       "ci": pos.get("source_citation_id"), "conf": pos.get("confidence"),
                       "id": str(existing["id"])})
            else:
                conn.execute(text("""
                    INSERT INTO fact_positions
                      (organization_id, case_id, fact_id, party_id, side, position,
                       source_claim_id, source_citation_id, confidence, created_by)
                    VALUES (:o,:c,:f,:p,:s,:pos,:sc,:ci,:conf,:u)
                """), {"o": org_id, "c": case_id, "f": fact_id, "p": party_id, "s": side,
                       "pos": pos.get("position"), "sc": pos.get("source_claim_id"),
                       "ci": pos.get("source_citation_id"), "conf": pos.get("confidence"), "u": actor_id})
    conn.execute(text("""
        UPDATE facts SET proposition=:p, proof_status=:ps, proof_basis=:pb,
          proven_by_decision_id=:dec, issue_id=:iid, fact_code=:fc,
          review_status='EDITED'
        WHERE id=:f
    """), {"p": fact["proposition"], "ps": fact["proof_status"], "pb": fact["proof_basis"],
           "dec": fact.get("proven_by_decision_id"), "iid": fact.get("issue_id"),
           "fc": fact.get("fact_code"), "f": fact_id})
    _revision(conn, fact, actor_id=actor_id, reason=reason)
    return get_fact(conn, case_id, fact_id, with_revisions=True) or fact


def review_fact(conn: Connection, *, org_id: str, case_id: str, fact_id: str,
                actor_id: str, action: str, reason: str, changes: dict[str, Any]) -> dict[str, Any]:
    fact = get_fact(conn, case_id, fact_id, with_revisions=False)
    if not fact:
        raise LookupError("fact_not_found")
    allowed = {"ACCEPT", "EDIT", "REJECT", "FLAG"}
    if action not in allowed:
        raise ValueError("invalid_review_action")
    conn.execute(text("""
        INSERT INTO reviews (organization_id, case_id, entity_type, entity_id, action,
                             reviewer_id, reason, original_output, human_output)
        VALUES (:o,:c,'fact',:f,:a,:u,:r,CAST(:orig AS jsonb),CAST(:human AS jsonb))
    """), {"o": org_id, "c": case_id, "f": fact_id, "a": action, "u": actor_id, "r": reason,
           "orig": json.dumps({k: fact.get(k) for k in ("proposition", "proof_status", "proof_basis", "version")},
                              ensure_ascii=False, default=str),
           "human": json.dumps(changes or {}, ensure_ascii=False, default=str)})
    result = get_fact(conn, case_id, fact_id, with_revisions=True)
    return result or fact
