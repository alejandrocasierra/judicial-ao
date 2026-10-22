"""Gate legal deterministico (modulos 1 y base de 2/3).

La regla es simple: una afirmacion solo se publica si su cita primaria existe,
contiene las cifras/fechas/nombres que afirma, las partes son del caso, y
cualquier norma o decision que invoque esta registrada. El juez semantico es una
capa posterior; no puede aprobar lo que esta capa rechaza.

El modulo es defensivo: si la migracion 0055 no esta aplicada o una consulta
auxiliar falla, devuelve un resultado `ERROR` sin romper el turno. El llamador
decide si publica o no.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows
from app.services import citations as citations_service, text_terms

log = logging.getLogger(__name__)

DEFAULT_POLICY: dict[str, Any] = {
    "id": None,
    "name": "Politica legal estandar",
    "mode": "BALANCED",
    "require_primary_citation": True,
    "require_legal_norm": True,
    "block_on_unsupported": True,
    "block_on_numbers": True,
    "block_on_parties": True,
    "block_on_legal_conclusion": True,
    "require_second_opinion": False,
    "second_opinion_model_id": None,
    "max_unsupported_claims": 0,
    "min_ocr_confidence": 0.55,
    "min_asr_confidence": 0.55,
    "allowed_jurisdictions": [],
    "version": 1,
}

_LAW_RE = re.compile(r"\b(?:ley|c[oó]digo|decreto|resoluci[oó]n)\s+(?:n(?:[uú]m(?:ero)?)?\.?\s*)?(\d{1,5}(?:\s+de\s+\d{4})?)", re.I)
_ARTICLE_RE = re.compile(r"\b(?:art(?:[ií]culo|\.)?)\s*(\d{1,4}[A-Za-z]?)\b", re.I)
_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+de\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?\d{2,4})\b",
    re.I,
)
_LEGAL_CONCLUSION_RE = re.compile(
    r"\b(nul(?:o|idad)|prescrip(?:ci[oó]n|to)|cosa juzgada|caduc(?:idad|ado)|"
    r"responsabilidad|condena|obligaci[oó]n|ineficacia|resoluci[oó]n|"
    r"probado|determinado|sentencia)\b",
    re.I,
)
_PROBATIVE_RE = re.compile(
    r"\b(?:hecho|hechos)\s+probados?|est[aá]\s+probado|queda\s+probado|"
    r"determinad[oa]\s+judicialmente|cosa\s+juzgada|ejecutoriad[oa]",
    re.I,
)


def _norm(value: str) -> str:
    return text_terms.norm(value or "")


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _source_label(item: dict[str, Any]) -> str:
    if item.get("document_id"):
        return f"documento {item.get('filename') or item.get('document_id')} p. {item.get('page_number')}"
    if item.get("media_id"):
        return f"media {item.get('filename') or item.get('media_id')} {item.get('start_ms')}-{item.get('end_ms')} ms"
    return str(item.get("handle") or "fuente")


def default_policy(mode: str | None = None) -> dict[str, Any]:
    p = dict(DEFAULT_POLICY)
    p["mode"] = (mode or get_settings().LEGAL_GUARD_MODE or "BALANCED").upper()
    return p


def load_policy(conn: Connection, org_id: str, case_id: str) -> dict[str, Any]:
    """Politica del caso; hereda la global; si no hay filas, defaults."""
    row = one(conn, """
        SELECT p.*
        FROM case_verification_settings s
        JOIN legal_guard_policies p ON p.id = s.policy_id
        WHERE s.organization_id = :o AND s.case_id = :c
        LIMIT 1
    """, o=org_id, c=case_id)
    if row:
        return _policy_dict(row)
    row = one(conn, """
        SELECT * FROM legal_guard_policies
        WHERE organization_id = :o AND case_id IS NULL AND is_default
        ORDER BY version DESC, created_at DESC LIMIT 1
    """, o=org_id)
    if row:
        return _policy_dict(row)
    return default_policy()


def _policy_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULT_POLICY)
    out.update({k: row.get(k) for k in out.keys() if k in row})
    out["id"] = row.get("id")
    out["case_id"] = row.get("case_id")
    out["is_default"] = bool(row.get("is_default"))
    out["second_opinion_model_id"] = row.get("second_opinion_model_id")
    out["version"] = int(row.get("version") or 1)
    return out


def _strip_code(value: str) -> str:
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii").lower()


def _extract_dates(value: str) -> set[str]:
    return {m.group(0).strip().lower() for m in _DATE_RE.finditer(value or "")}


def _extract_norms(value: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in _LAW_RE.finditer(value or ""):
        out.append({"kind": "law", "identifier": m.group(1).strip(), "raw": m.group(0)})
    for m in _ARTICLE_RE.finditer(value or ""):
        out.append({"kind": "article", "identifier": m.group(1).strip(), "raw": m.group(0)})
    # Deduplicar por tipo+identificador.
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for n in out:
        key = (n["kind"], n["identifier"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(n)
    return unique


def _lookup_legal_rule(conn: Connection, org_id: str, jurisdiction: str, norm: dict[str, str]) -> dict[str, Any] | None:
    ident = norm["identifier"]
    like = f"%{ident}%"
    return one(conn, """
        SELECT id, jurisdiction, source, identifier, title, version_date
        FROM legal_rules
        WHERE organization_id = :o AND is_active
          AND (jurisdiction = :j OR :j = '')
          AND (identifier ILIKE :q OR coalesce(title,'') ILIKE :q OR coalesce(text,'') ILIKE :q)
        ORDER BY version_date DESC NULLS LAST
        LIMIT 1
    """, o=org_id, j=jurisdiction or "", q=like)


def _party_names(conn: Connection, case_id: str) -> list[dict[str, Any]]:
    return rows(conn, "SELECT id, name, role, aliases FROM parties WHERE case_id = :c", c=case_id)


def _cite_check(conn: Connection, case_id: str, item: dict[str, Any]) -> tuple[bool, list[str]]:
    if item.get("document_id") and item.get("page_number") is not None:
        citation = {
            "case_id": case_id, "source_type": "document_page",
            "document_id": item.get("document_id"), "page_number": item.get("page_number"),
        }
        ok, reasons, _ = citations_service.validate(conn, citation)
        return ok, reasons
    if item.get("media_id") and item.get("segment_id") and item.get("start_ms") is not None:
        citation = {
            "case_id": case_id, "source_type": "transcript_segment",
            "media_id": item.get("media_id"), "segment_id": item.get("segment_id"),
            "start_ms": item.get("start_ms"), "end_ms": item.get("end_ms"),
        }
        ok, reasons, _ = citations_service.validate(conn, citation)
        return ok, reasons
    return False, ["source_not_resolvable"]


def _add_finding(findings: list[dict[str, Any]], *, code: str, severity: str, message: str,
                 claim_index: int | None = None, source_type: str | None = None,
                 source_id: str | None = None, blocking: bool | None = None,
                 metadata: dict[str, Any] | None = None) -> None:
    blocking = bool(severity == "CRITICAL") if blocking is None else blocking
    findings.append({
        "claim_index": claim_index, "code": code, "severity": severity,
        "message": message, "source_type": source_type, "source_id": source_id,
        "blocking": blocking, "metadata": metadata or {},
    })


def audit(conn: Connection, *, org_id: str, case_id: str, question: str, answer: str,
          claims: list[dict[str, Any]], items: list[dict[str, Any]],
          policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Audita claims contra las fuentes. No escribe en BD."""
    policy = policy or load_policy(conn, org_id, case_id)
    by_handle = {str(it.get("handle")): it for it in (items or []) if it.get("handle")}
    parties = _party_names(conn, case_id)
    party_norms = []
    for p in parties:
        names = [p.get("name")] + list(p.get("aliases") or [])
        party_norms.append((p, [_norm(str(n)) for n in names if n]))
    decisions = rows(conn, "SELECT id, source_document_id, outcome FROM decisions WHERE case_id = :c", c=case_id)
    jurisdiction = ""
    # `cases.jurisdiction` es un codigo (p. ej. co); se usa para normas.
    case = one(conn, "SELECT jurisdiction FROM cases WHERE id = :c", c=case_id)
    if case:
        jurisdiction = str(case.get("jurisdiction") or "")
    allowed = policy.get("allowed_jurisdictions") or []
    if allowed and jurisdiction and jurisdiction not in allowed:
        findings: list[dict[str, Any]] = []
        _add_finding(findings, code="JURISDICTION_MISMATCH", severity="CRITICAL",
                     message=f"El proceso es de {jurisdiction} y la politica solo permite {allowed}.")
    else:
        findings = []
    claim_results: list[dict[str, Any]] = []
    citation_checks: list[dict[str, Any]] = []
    approved: list[dict[str, Any]] = []
    limits: list[str] = []
    blocked_count = 0

    for index, raw in enumerate(claims or []):
        text_claim = str(raw.get("text") or "").strip()
        if not text_claim:
            continue
        handles = [str(x) for x in (raw.get("citations") or []) if str(x)]
        claim_type = str(raw.get("claim_type") or "").upper()
        if claim_type not in {"FACT", "LEGAL_CONCLUSION", "PROCEDURAL", "PARTY_ALLEGATION", "INFERENCE"}:
            claim_type = "LEGAL_CONCLUSION" if _LEGAL_CONCLUSION_RE.search(text_claim) else "FACT"
        missing_numbers: list[str] = []
        missing_dates: list[str] = []
        missing_parties: list[str] = []
        legal_rule_ids: list[str] = []
        reasons: list[dict[str, Any]] = []
        critical = False
        high = False

        if not handles:
            _add_finding(findings, code="CITATION_MISSING", severity="CRITICAL",
                         message="La afirmacion no tiene cita primaria.", claim_index=index)
            reasons.append({"code": "CITATION_MISSING", "detail": "sin cita"})
            critical = True
        else:
            cited_items: list[dict[str, Any]] = []
            for handle in handles:
                item = by_handle.get(handle)
                if item is None:
                    _add_finding(findings, code="CITATION_UNRESOLVED", severity="CRITICAL",
                                 message=f"La cita {handle} no resuelve a una fuente del caso.",
                                 claim_index=index, source_id=handle)
                    reasons.append({"code": "CITATION_UNRESOLVED", "handle": handle})
                    critical = True
                    continue
                ok, cite_reasons = _cite_check(conn, case_id, item)
                citation_checks.append({
                    "citation_id": None, "valid": ok, "reasons": cite_reasons,
                    "source_type": item.get("source_type"), "source_id": item.get("document_id") or item.get("media_id"),
                    "page_number": item.get("page_number"), "segment_id": item.get("segment_id"),
                })
                if not ok:
                    _add_finding(findings, code="CITATION_UNRESOLVED", severity="CRITICAL",
                                 message=f"La cita {handle} no coincide con la fuente ({', '.join(cite_reasons)}).",
                                 claim_index=index, source_type=item.get("source_type"),
                                 source_id=item.get("document_id") or item.get("media_id"))
                    reasons.append({"code": "CITATION_UNRESOLVED", "handle": handle, "detail": cite_reasons})
                    critical = True
                cited_items.append(item)

            cited_text = " ".join(str(it.get("text") or "") for it in cited_items)
            cited_norm = _norm(cited_text)
            # Cifras: cualquier numero del claim debe aparecer en la fuente.
            for number in text_terms.numbers(text_claim):
                if number not in text_terms.numbers(cited_text):
                    missing_numbers.append(str(number))
            if missing_numbers:
                _add_finding(findings, code="NUMBER_MISMATCH", severity="CRITICAL",
                             message=f"Cifras no soportadas por la cita: {', '.join(missing_numbers)}.",
                             claim_index=index)
                reasons.append({"code": "NUMBER_MISMATCH", "missing": missing_numbers})
                critical = bool(policy.get("block_on_numbers", True))
            # Fechas: fecha explicita del claim debe aparecer en la fuente.
            for date_value in _extract_dates(text_claim):
                if _norm(date_value) not in cited_norm:
                    missing_dates.append(date_value)
            if missing_dates:
                _add_finding(findings, code="DATE_MISMATCH", severity="CRITICAL",
                             message=f"Fechas no soportadas por la cita: {', '.join(missing_dates)}.",
                             claim_index=index)
                reasons.append({"code": "DATE_MISMATCH", "missing": missing_dates})
                critical = bool(policy.get("block_on_numbers", True))
            # Partes conocidas: si el claim nombra una parte y la cita no la
            # contiene, no se puede afirmar que esa parte hizo algo.
            for party, names in party_norms:
                if any(n and n in _norm(text_claim) for n in names):
                    if not any(n and n in cited_norm for n in names):
                        missing_parties.append(str(party.get("name")))
            if missing_parties and policy.get("block_on_parties", True):
                _add_finding(findings, code="PARTY_MISMATCH", severity="HIGH",
                             message=f"Partes no soportadas por la cita: {', '.join(missing_parties)}.",
                             claim_index=index)
                reasons.append({"code": "PARTY_MISMATCH", "missing": missing_parties})
                high = True
            # OCR/ASR bajo: no se usa para probar.
            for item in cited_items:
                conf = item.get("ocr_confidence") if item.get("document_id") else item.get("asr_confidence")
                threshold = float(policy.get("min_ocr_confidence", 0.55) if item.get("document_id")
                                  else policy.get("min_asr_confidence", 0.55))
                if conf is not None and float(conf) < threshold:
                    code = "LOW_OCR_CONFIDENCE" if item.get("document_id") else "LOW_ASR_CONFIDENCE"
                    _add_finding(findings, code=code, severity="HIGH",
                                 message=f"La fuente {item.get('handle')} tiene confianza baja ({conf}).",
                                 claim_index=index, source_type=item.get("source_type"),
                                 source_id=item.get("document_id") or item.get("media_id"))
                    reasons.append({"code": code, "confidence": conf})
                    high = True
                    break

        # Normas: toda referencia a ley/articulo debe estar en legal_rules.
        norms = _extract_norms(text_claim)
        if norms and policy.get("require_legal_norm", True):
            for norm in norms:
                rule = _lookup_legal_rule(conn, org_id, jurisdiction, norm)
                if rule is None:
                    _add_finding(findings, code="LEGAL_RULE_MISSING", severity="CRITICAL",
                                 message=f"Norma no verificada en legal_rules: {norm['raw']}.",
                                 claim_index=index, metadata={"norm": norm})
                    reasons.append({"code": "LEGAL_RULE_MISSING", "norm": norm})
                    critical = bool(policy.get("block_on_legal_conclusion", True))
                else:
                    legal_rule_ids.append(str(rule["id"]))

        # Decision: afirmar que algo esta probado/determinado exige decision.
        if _PROBATIVE_RE.search(text_claim) and policy.get("block_on_legal_conclusion", True):
            if not decisions and claim_type in {"FACT", "LEGAL_CONCLUSION"}:
                _add_finding(findings, code="DECISION_MISSING", severity="CRITICAL",
                             message="Se afirma un hecho probado/determinado sin decision judicial.",
                             claim_index=index)
                reasons.append({"code": "DECISION_MISSING"})
                critical = True

        if critical:
            status = "UNSUPPORTED"
            blocked_count += 1
        elif high:
            status = "NEEDS_REVIEW"
            limits.append(f"La afirmacion {index + 1} requiere revision: {text_claim[:160]}")
        else:
            status = "SUPPORTED"
            approved.append({"text": text_claim, "citations": handles, **(
                {"claim_type": claim_type} if claim_type else {})})
        claim_results.append({
            "claim_index": index, "text": text_claim, "claim_type": claim_type,
            "citations": handles, "status": status,
            "missing_numbers": missing_numbers, "missing_dates": missing_dates,
            "missing_parties": missing_parties, "legal_rule_ids": legal_rule_ids,
            "reasons": reasons,
        })

    critical_count = sum(1 for f in findings if f["severity"] == "CRITICAL")
    if critical_count and not approved:
        status = "BLOCKED"
    elif critical_count or any(f["severity"] == "HIGH" for f in findings):
        status = "WARNING"
    else:
        status = "PASSED"
    if status == "WARNING" and policy.get("mode") == "STRICT" and not approved:
        status = "NEEDS_REVIEW"

    return {
        "status": status,
        "mode": policy.get("mode", "BALANCED"),
        "policy": policy,
        "claims": claim_results,
        "approved_claims": approved,
        "findings": findings,
        "limits": limits,
        "claims_total": len(claim_results),
        "claims_supported": sum(1 for c in claim_results if c["status"] == "SUPPORTED"),
        "claims_blocked": blocked_count,
        "findings_critical": critical_count,
        "citation_checks": citation_checks,
        "evidence_snapshot": [
            {"handle": it.get("handle"), "source_type": it.get("source_type"),
             "document_id": it.get("document_id"), "page_number": it.get("page_number"),
             "media_id": it.get("media_id"), "segment_id": it.get("segment_id"),
             "text": str(it.get("text") or "")[:4000]}
            for it in (items or [])[:40]
        ],
    }


def persist(conn: Connection, *, org_id: str, case_id: str, question: str, answer: str,
            result: dict[str, Any], model_run_id: str | None = None,
            chat_message_id: str | None = None, actor_id: str | None = None,
            pipeline_version: str = "", prompt_version: str | None = None,
            idempotency_extra: str = "") -> str | None:
    """Persiste una auditoria append-only. Devuelve el id o None si no aplica."""
    if not result:
        return None
    policy = result.get("policy") or default_policy()
    answer_hash = _hash(answer or "")
    key_material = f"{case_id}|{model_run_id or ''}|{chat_message_id or ''}|{answer_hash}|{policy.get('version')}|{idempotency_extra}"
    idem = _hash(key_material)
    existing = one(conn, """
        SELECT id FROM answer_audits
        WHERE organization_id = :o AND idempotency_key = :k
    """, o=org_id, k=idem)
    if existing:
        return str(existing["id"])
    audit_id = one(conn, """
        INSERT INTO answer_audits
          (organization_id, case_id, chat_message_id, model_run_id, policy_id,
           question, answer_hash, answer_excerpt, status, mode, claims_total,
           claims_supported, claims_blocked, findings_critical, evidence_hash,
           evidence_snapshot, details, pipeline_version, prompt_version,
           duration_ms, idempotency_key, created_by)
        VALUES
          (:o, :c, :m, :mr, :p, :q, :ah, :ae, :st, :mode, :ct, :cs, :cb, :fc,
           :eh, CAST(:es AS jsonb), CAST(:det AS jsonb), :pv, :prompt, :dur, :idem, :u)
        RETURNING id
    """, o=org_id, c=case_id, m=chat_message_id, mr=model_run_id,
         p=policy.get("id"), q=question[:8000], ah=answer_hash, ae=(answer or "")[:4000],
         st=result.get("status", "ERROR"), mode=result.get("mode", "BALANCED"),
         ct=int(result.get("claims_total") or 0), cs=int(result.get("claims_supported") or 0),
         cb=int(result.get("claims_blocked") or 0), fc=int(result.get("findings_critical") or 0),
         eh=_hash(json.dumps(result.get("evidence_snapshot") or [], sort_keys=True, default=str)),
         es=json.dumps(result.get("evidence_snapshot") or [], ensure_ascii=False, default=str),
         det=json.dumps({"limits": result.get("limits") or []}, ensure_ascii=False, default=str),
         pv=pipeline_version, prompt=prompt_version, dur=result.get("duration_ms"),
         idem=idem, u=actor_id)
    audit_id = str(audit_id["id"])
    for c in result.get("claims") or []:
        conn.execute(text("""
            INSERT INTO answer_audit_claims
              (organization_id, audit_id, claim_index, claim_text, claim_hash, claim_type,
               citation_handles, status, grounding_score, missing_numbers, missing_dates,
               missing_parties, legal_rule_ids, reasons, auditor)
            VALUES
              (:o, :a, :i, :t, :h, :ct, CAST(:ch AS text[]), :st, :gs,
               CAST(:mn AS text[]), CAST(:md AS text[]), CAST(:mp AS text[]),
               CAST(:lr AS uuid[]), CAST(:rs AS jsonb), 'DETERMINISTIC')
        """), {"o": org_id, "a": audit_id, "i": int(c.get("claim_index") or 0), "t": c.get("text") or "",
               "h": _hash(c.get("text") or ""), "ct": c.get("claim_type") or "FACT",
               "ch": "{" + ",".join(c.get("citations") or []) + "}",
               "st": c.get("status") or "NEEDS_REVIEW", "gs": 1.0 if c.get("status") == "SUPPORTED" else 0.0,
               "mn": "{" + ",".join(c.get("missing_numbers") or []) + "}",
               "md": "{" + ",".join(c.get("missing_dates") or []) + "}",
               "mp": "{" + ",".join(c.get("missing_parties") or []) + "}",
               "lr": "{" + ",".join(c.get("legal_rule_ids") or []) + "}",
               "rs": json.dumps(c.get("reasons") or [], ensure_ascii=False, default=str)})
    for cit in result.get("citation_checks") or []:
        conn.execute(text("""
            INSERT INTO answer_audit_citations
              (organization_id, audit_id, citation_id, valid, reasons, source_type,
               source_id, page_number, segment_id, quote_hash)
            VALUES (:o, :a, :ci, :v, CAST(:r AS text[]), :st, :sid, :pg, :sg, :qh)
        """), {"o": org_id, "a": audit_id, "ci": cit.get("citation_id"), "v": bool(cit.get("valid")),
               "r": "{" + ",".join(cit.get("reasons") or []) + "}", "st": cit.get("source_type"),
               "sid": cit.get("source_id"), "pg": cit.get("page_number"), "sg": cit.get("segment_id"),
               "qh": cit.get("quote_hash")})
    for f in result.get("findings") or []:
        conn.execute(text("""
            INSERT INTO answer_audit_findings
              (organization_id, audit_id, claim_index, code, severity, message,
               source_type, source_id, blocking, metadata)
            VALUES (:o, :a, :ci, :code, :sev, :msg, :st, :sid, :b, CAST(:meta AS jsonb))
        """), {"o": org_id, "a": audit_id, "ci": f.get("claim_index"), "code": f.get("code"),
               "sev": f.get("severity"), "msg": f.get("message"), "st": f.get("source_type"),
               "sid": f.get("source_id"), "b": bool(f.get("blocking")),
               "meta": json.dumps(f.get("metadata") or {}, ensure_ascii=False, default=str)})
    return audit_id


def audit_and_persist(conn: Connection, **kwargs) -> dict[str, Any]:
    result = audit(conn, **{k: kwargs[k] for k in ("org_id", "case_id", "question", "answer", "claims", "items", "policy")
                            if k in kwargs})
    audit_id = persist(conn, **{k: kwargs[k] for k in (
        "org_id", "case_id", "question", "answer", "model_run_id", "chat_message_id",
        "actor_id", "pipeline_version", "prompt_version", "idempotency_extra") if k in kwargs},
        result=result)
    result["audit_id"] = audit_id
    return result


def audit_message(conn: Connection, *, org_id: str, case_id: str, message_id: str,
                  actor_id: str | None = None) -> dict[str, Any] | None:
    """Reaudita un mensaje usando los claims de su model_run y sus citas.

    Es la via para mensajes historicos. No inventa evidencia: si el mensaje no
    tiene model_run/claims, devuelve None.
    """
    msg = one(conn, """
        SELECT m.id, m.content, m.model_run_id, m.citations, r.output
        FROM chat_messages m LEFT JOIN model_runs r ON r.id = m.model_run_id
        WHERE m.id = :m AND m.organization_id = :o
    """, m=message_id, o=org_id)
    if not msg or not msg.get("output"):
        return None
    try:
        output = msg["output"] if isinstance(msg["output"], dict) else json.loads(msg["output"])
    except Exception:
        return None
    raw_claims = output.get("claims") or []
    claims: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    citations = msg.get("citations") if isinstance(msg.get("citations"), list) else []
    by_id = {str(c.get("citation_id")): c for c in citations if isinstance(c, dict)}
    for _idx, cl in enumerate(raw_claims):
        if not isinstance(cl, dict):
            continue
        handles = []
        for cid in cl.get("citations") or []:
            cite = by_id.get(str(cid))
            if not cite:
                continue
            handle = f"H{len(items) + 1}"
            item = {"handle": handle, "source_type": cite.get("source_type")}
            if cite.get("document_id"):
                item.update({"document_id": cite.get("document_id"), "page_number": cite.get("page")})
                page = one(conn, """SELECT p.text, p.folio, p.ocr_confidence, d.filename
                                     FROM document_pages p JOIN documents d ON d.id=p.document_id
                                     WHERE p.document_id=:d AND p.page_number=:n""",
                           d=str(cite.get("document_id")), n=cite.get("page"))
                if page:
                    item.update({"text": page.get("text") or "", "folio": page.get("folio"),
                                 "ocr_confidence": page.get("ocr_confidence"),
                                 "filename": page.get("filename")})
            else:
                item.update({"media_id": cite.get("media_id"),
                             "segment_id": cite.get("segment_id"),
                             "start_ms": cite.get("start_ms"), "end_ms": cite.get("end_ms")})
                seg = one(conn, """SELECT s.text, m.filename FROM transcript_segments s
                                    JOIN media m ON m.id=s.media_id WHERE s.id=:s""",
                          s=str(cite.get("segment_id")))
                if seg:
                    item.update({"text": seg.get("text") or "", "filename": seg.get("filename")})
            items.append(item)
            handles.append(handle)
        claims.append({"text": cl.get("text") or "", "citations": handles, "claim_type": cl.get("claim_type")})
    result = audit(conn, org_id=org_id, case_id=case_id, question="", answer=msg["content"],
                   claims=claims, items=items)
    audit_id = persist(conn, org_id=org_id, case_id=case_id, question="", answer=msg["content"],
                       result=result, model_run_id=str(msg.get("model_run_id")) if msg.get("model_run_id") else None,
                       chat_message_id=str(message_id), actor_id=actor_id,
                       pipeline_version=get_settings().PIPELINE_VERSION,
                       idempotency_extra=f"reaudit:{message_id}")
    result["audit_id"] = audit_id
    return result
