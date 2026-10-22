"""Segunda opinion por proceso/demanda.

Fase 1: el verificador responde de forma independiente, sin ver la respuesta
original. Fase 2: se comparan claims, citas, cifras y normas. El verificador no
tiene tools de escritura: su unico efecto es registrar su propia corrida.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows
from app.providers.llm import get_llm_for_model
from app.services import legal_guard, text_terms

log = logging.getLogger(__name__)

_PROMPT_FALLBACK = """Eres un auditor juridico independiente. Responde SOLO con la evidencia
suministrada. Devuelve JSON: {"claims":[{"text":"...","citations":["E1"]}],"uncertainties":[]}.
No inventes normas, partes, cifras ni decisiones. Si la evidencia no alcanza, agrega una incertidumbre."""


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _load_prompt(name: str) -> str:
    s = get_settings()
    path = s.path(s.PROMPTS_DIR) / name
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else raw.strip()
    return _PROMPT_FALLBACK


def _evidence_block(snapshot: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, it in enumerate(snapshot or [], start=1):
        handle = it.get("handle") or f"E{i}"
        text = str(it.get("text") or "")[:6000]
        src = it.get("source_type") or "fuente"
        lines.append(f'<evidence id="{handle}" source="{src}">\n{text}\n</evidence>')
    return "\n".join(lines)


def choose_verifier(conn: Connection, org_id: str, case_id: str,
                    responder_model_id: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    configured = one(conn, """
        SELECT s.second_opinion_model_id
        FROM case_verification_settings s
        WHERE s.organization_id=:o AND s.case_id=:c
    """, o=org_id, c=case_id)
    preferred = None
    if configured:
        preferred = configured.get("second_opinion_model_id")
    preferred = preferred or settings.SECOND_OPINION_MODEL_ID
    if preferred:
        model = one(conn, "SELECT * FROM ai_models WHERE id=:i AND organization_id=:o",
                    i=str(preferred), o=org_id)
        if model:
            if responder_model_id and str(model["id"]) == str(responder_model_id):
                raise ValueError("second_opinion_model_must_differ")
            return model
    candidates = rows(conn, """
        SELECT * FROM ai_models WHERE organization_id=:o
        ORDER BY is_default DESC, created_at ASC
    """, o=org_id)
    for model in candidates:
        if responder_model_id and str(model["id"]) == str(responder_model_id):
            continue
        return model
    raise ValueError("second_opinion_model_required")


def _message_context(conn: Connection, org_id: str, case_id: str, message_id: str) -> dict[str, Any] | None:
    return one(conn, """
        SELECT m.id, m.content, m.model_run_id, m.citations, s.model_id AS session_model_id,
               r.provider AS responder_provider, r.model AS responder_model,
               a.id AS audit_id, a.question, a.evidence_snapshot, a.answer_excerpt
        FROM chat_messages m
        JOIN chat_sessions s ON s.id = m.session_id
        LEFT JOIN model_runs r ON r.id = m.model_run_id
        LEFT JOIN LATERAL (
          SELECT aa.* FROM answer_audits aa
          WHERE aa.organization_id=:o AND aa.case_id=:c AND aa.model_run_id=m.model_run_id
          ORDER BY aa.created_at DESC LIMIT 1
        ) a ON true
        WHERE m.id=:m AND m.organization_id=:o AND m.role='assistant'
    """, o=org_id, c=case_id, m=message_id)


def ensure_audit(conn: Connection, org_id: str, case_id: str, message_id: str,
                 actor_id: str | None = None) -> str:
    ctx = _message_context(conn, org_id, case_id, message_id)
    if not ctx:
        raise LookupError("message_not_found")
    if ctx.get("audit_id"):
        return str(ctx["audit_id"])
    result = legal_guard.audit_message(conn, org_id=org_id, case_id=case_id,
                                       message_id=message_id, actor_id=actor_id)
    if not result or not result.get("audit_id"):
        raise ValueError("answer_audit_required")
    return str(result["audit_id"])


def create_run(conn: Connection, *, org_id: str, case_id: str, message_id: str,
               actor_id: str | None) -> dict[str, Any]:
    ctx = _message_context(conn, org_id, case_id, message_id)
    if not ctx:
        raise LookupError("message_not_found")
    audit_id = ensure_audit(conn, org_id, case_id, message_id, actor_id)
    responder = str(ctx.get("session_model_id") or "")
    model = choose_verifier(conn, org_id, case_id, responder or None)
    # Si no hay id de sesion, comparar por nombre de modelo para no usar el mismo.
    if not responder and ctx.get("responder_model"):
        if (str(model.get("provider")) == str(ctx.get("responder_provider"))
                and str(model.get("model_name")) == str(ctx.get("responder_model"))):
            raise ValueError("second_opinion_model_must_differ")
    idem = _hash(f"{case_id}|{message_id}|{model['id']}|{audit_id}")
    existing = one(conn, """
        SELECT * FROM second_opinion_runs WHERE organization_id=:o AND idempotency_key=:k
    """, o=org_id, k=idem)
    if existing:
        return existing
    run = one(conn, """
        INSERT INTO second_opinion_runs
          (organization_id, case_id, audit_id, chat_message_id, responder_model_run_id,
           verifier_model_id, verifier_provider, verifier_model_name, status,
           idempotency_key, created_by)
        VALUES (:o,:c,:a,:m,:rm,:vm,:vp,:vn,'QUEUED',:k,:u)
        RETURNING *
    """, o=org_id, c=case_id, a=audit_id, m=message_id, rm=ctx.get("model_run_id"),
          vm=model["id"], vp=model.get("provider"), vn=model.get("model_name"),
          k=idem, u=actor_id)
    return run


def get_run(conn: Connection, org_id: str, case_id: str, run_id: str) -> dict[str, Any] | None:
    run = one(conn, "SELECT * FROM second_opinion_runs WHERE id=:r AND organization_id=:o AND case_id=:c",
              r=run_id, o=org_id, c=case_id)
    if not run:
        return None
    out = dict(run)
    out["claims"] = rows(conn, "SELECT * FROM second_opinion_claims WHERE run_id=:r ORDER BY claim_index",
                         r=run_id)
    out["findings"] = rows(conn, "SELECT * FROM second_opinion_findings WHERE run_id=:r ORDER BY id",
                           r=run_id)
    return out


def list_runs(conn: Connection, org_id: str, case_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return rows(conn, """
        SELECT r.*, m.content AS answer_excerpt
        FROM second_opinion_runs r
        LEFT JOIN chat_messages m ON m.id=r.chat_message_id
        WHERE r.organization_id=:o AND r.case_id=:c
        ORDER BY r.created_at DESC LIMIT :l
    """, o=org_id, c=case_id, l=max(1, min(int(limit), 200)))


def _parse_claims(raw: str) -> dict[str, Any]:
    txt = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M).strip()
    try:
        data = json.loads(txt)
    except Exception:
        return {"claims": [], "uncertainties": ["El verificador no devolvio JSON valido."], "raw": raw}
    if not isinstance(data, dict):
        return {"claims": [], "uncertainties": ["El verificador no devolvio un objeto JSON."], "raw": raw}
    claims = []
    for c in data.get("claims") or []:
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            claims.append({"text": c["text"], "citations": [str(x) for x in (c.get("citations") or [])]})
    return {"claims": claims, "uncertainties": data.get("uncertainties") or [], "raw": raw}


def compare(original: list[dict[str, Any]], second: list[dict[str, Any]]) -> dict[str, Any]:
    """Comparacion determinista por claim, cifras y citas."""
    original = [c for c in (original or []) if str(c.get("text") or "").strip()]
    second = [c for c in (second or []) if str(c.get("text") or "").strip()]
    used: set[int] = set()
    differences: list[dict[str, Any]] = []
    matches = 0
    for oi, oc in enumerate(original):
        onorm = text_terms.norm(str(oc.get("text") or ""))
        best: tuple[float, int] | None = None
        for si, sc in enumerate(second):
            if si in used:
                continue
            snorm = text_terms.norm(str(sc.get("text") or ""))
            if onorm == snorm:
                best = (1.0, si)
                break
            a, b = set(text_terms.content_terms(onorm)), set(text_terms.content_terms(snorm))
            score = len(a & b) / max(1, len(a | b))
            if best is None or score > best[0]:
                best = (score, si)
        if best and best[0] >= 0.45:
            used.add(best[1])
            matches += 1
            sc = second[best[1]]
            o_nums = set(text_terms.numbers(str(oc.get("text") or "")))
            s_nums = set(text_terms.numbers(str(sc.get("text") or "")))
            if o_nums != s_nums:
                differences.append({"claim_index": oi, "type": "DIFFERENT_NUMBER",
                                    "severity": "HIGH", "original": sorted(o_nums),
                                    "second": sorted(s_nums)})
            else:
                differences.append({"claim_index": oi, "type": "MATCH", "severity": "LOW"})
        else:
            differences.append({"claim_index": oi, "type": "MISSING", "severity": "MEDIUM",
                                "original": oc.get("text")})
    for si, sc in enumerate(second):
        if si not in used:
            differences.append({"claim_index": len(original) + si, "type": "EXTRA",
                                "severity": "MEDIUM", "second": sc.get("text")})
    if not original and not second:
        verdict = "INCONCLUSIVE"
    elif any(d["type"] == "DIFFERENT_NUMBER" for d in differences):
        verdict = "DISAGREE"
    elif any(d["type"] in {"MISSING", "EXTRA"} for d in differences):
        verdict = "PARTIAL"
    elif matches == len(original) and matches == len(second):
        verdict = "AGREE"
    else:
        verdict = "INCONCLUSIVE"
    return {"verdict": verdict, "differences": differences, "matches": matches,
            "original_total": len(original), "second_total": len(second)}


def _cost(tokens_in: int, tokens_out: int) -> float:
    # Coste conservador y configurable; no inventa precios del proveedor.
    return round((tokens_in * 0.0000005) + (tokens_out * 0.0000015), 6)


def execute_run(conn: Connection, run_id: str) -> dict[str, Any]:
    """Ejecuta una corrida QUEUED. Idempotente para COMPLETED/FAILED."""
    run = one(conn, "SELECT * FROM second_opinion_runs WHERE id=:r FOR UPDATE", r=run_id)
    if not run:
        raise LookupError("second_opinion_run_not_found")
    if run["status"] in {"COMPLETED", "CANCELLED", "RUNNING"}:
        return {"run_id": run_id, "status": run["status"], "skipped": True}
    started = time.monotonic()
    conn.execute(text("UPDATE second_opinion_runs SET status='RUNNING', started_at=now() WHERE id=:r"),
                 {"r": run_id})
    try:
        audit = one(conn, "SELECT * FROM answer_audits WHERE id=:a", a=run["audit_id"])
        msg = one(conn, "SELECT content FROM chat_messages WHERE id=:m", m=run["chat_message_id"])
        if not audit or not msg:
            raise ValueError("second_opinion_context_missing")
        snapshot = audit.get("evidence_snapshot") or []
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        system = _load_prompt("second_opinion_answer.v1.md")
        user = (f"<question>{audit.get('question') or msg.get('content') or ''}</question>\n"
                + _evidence_block(snapshot)
                + "\nDevuelve solo JSON con claims y citas de los handles E1..En.")
        llm = get_llm_for_model(str(run["verifier_model_id"]), str(run["organization_id"]),
                                str(run.get("created_by") or ""))
        result = llm.complete(system, user)
        parsed = _parse_claims(getattr(result, "text", str(result)))
        original_claims = rows(conn, """
            SELECT claim_index, claim_text AS text, citation_handles AS citations
            FROM answer_audit_claims WHERE audit_id=:a AND auditor='DETERMINISTIC'
            ORDER BY claim_index
        """, a=run["audit_id"])
        comp = compare(original_claims, parsed["claims"])
        duration = int((time.monotonic() - started) * 1000)
        tokens_in = int(getattr(result, "tokens_in", 0) or 0)
        tokens_out = int(getattr(result, "tokens_out", 0) or 0)
        validation = {"schema_valid": bool(parsed["claims"]), "verdict": comp["verdict"]}
        model_run = one(conn, """
            INSERT INTO model_runs
              (organization_id, case_id, task, provider, model, prompt_id, prompt_version,
               pipeline_version, input_hash, output, validation, tokens_in, tokens_out, actor_id)
            VALUES (:o,:c,'second_opinion',:p,:m,'second_opinion_answer',1,:pv,:ih,
                    CAST(:out AS jsonb),CAST(:val AS jsonb),:ti,:to,:u)
            RETURNING id
        """, o=str(run["organization_id"]), c=str(run["case_id"]), p=run.get("verifier_provider"),
              m=run.get("verifier_model_name"), pv=get_settings().PIPELINE_VERSION,
              ih=_hash(audit.get("answer_hash") or ""),
              out=json.dumps({"independent": parsed, "comparison": comp}, ensure_ascii=False, default=str),
              val=json.dumps(validation, ensure_ascii=False, default=str),
              ti=tokens_in, to=tokens_out, u=run.get("created_by"))
        conn.execute(text("""
            UPDATE second_opinion_runs
            SET status='COMPLETED', verdict=:v, summary=:s, output=CAST(:out AS jsonb),
                model_run_id=:mr, tokens_in=:ti, tokens_out=:to, cost=:cost,
                duration_ms=:dur, completed_at=now()
            WHERE id=:r
        """), {"v": comp["verdict"], "s": _summary(comp), "out": json.dumps(parsed, ensure_ascii=False, default=str),
               "mr": str(model_run["id"]), "ti": tokens_in, "to": tokens_out,
               "cost": _cost(tokens_in, tokens_out), "dur": duration, "r": run_id})
        for i, claim in enumerate(parsed["claims"]):
            diff = next((d for d in comp["differences"] if d.get("claim_index") == i), None)
            comparison = ("CONTRADICTED" if diff and diff.get("type") == "DIFFERENT_NUMBER"
                          else "MATCH" if diff and diff.get("type") == "MATCH"
                          else "EXTRA" if diff and diff.get("type") == "EXTRA" else "UNCERTAIN")
            conn.execute(text("""
                INSERT INTO second_opinion_claims
                  (organization_id, run_id, claim_index, claim_text, claim_hash, citations,
                   status, comparison, reasons)
                VALUES (:o,:r,:i,:t,:h,CAST(:c AS jsonb),'SUPPORTED',:cmp,CAST(:rs AS jsonb))
                ON CONFLICT (run_id, claim_index) DO NOTHING
            """), {"o": str(run["organization_id"]), "r": run_id, "i": i, "t": claim["text"],
                   "h": _hash(claim["text"]), "c": json.dumps(claim.get("citations") or [], ensure_ascii=False),
                   "cmp": comparison, "rs": json.dumps(diff or {}, ensure_ascii=False, default=str)})
        for diff in comp["differences"]:
            if diff.get("type") in {"DIFFERENT_NUMBER", "MISSING", "EXTRA"}:
                conn.execute(text("""
                    INSERT INTO second_opinion_findings
                      (organization_id, run_id, claim_index, code, severity, message, metadata)
                    VALUES (:o,:r,:i,:code,:sev,:msg,CAST(:m AS jsonb))
                """), {"o": str(run["organization_id"]), "r": run_id, "i": diff.get("claim_index"),
                       "code": f"MODEL_{diff['type']}", "sev": diff.get("severity", "MEDIUM"),
                       "msg": f"El segundo modelo {diff.get('type','')} en el claim {diff.get('claim_index')}.",
                       "m": json.dumps(diff, ensure_ascii=False, default=str)})
        return {"run_id": run_id, "status": "COMPLETED", "verdict": comp["verdict"]}
    except Exception as exc:  # noqa: BLE001
        log.exception("second opinion run failed")
        conn.execute(text("""
            UPDATE second_opinion_runs SET status='FAILED', error_code='SECOND_OPINION_ERROR',
              error_detail=:e, completed_at=now() WHERE id=:r
        """), {"e": str(exc)[:1000], "r": run_id})
        return {"run_id": run_id, "status": "FAILED", "error": str(exc)}


def _summary(comp: dict[str, Any]) -> str:
    return (f"{comp.get('verdict')}: {comp.get('matches')} coincidencias de "
            f"{comp.get('original_total')} claims originales y {comp.get('second_total')} del verificador.")
