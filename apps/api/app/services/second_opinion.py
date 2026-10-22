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
               actor_id: str | None, enforce_budget: bool = True) -> dict[str, Any]:
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
    snapshot = ctx.get("evidence_snapshot") or []
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    estimate = estimate_run_cost(snapshot, str(ctx.get("question") or ""), model)
    if enforce_budget and estimate["cost"] > estimate["limit"]:
        raise ValueError("second_opinion_cost_exceeded")
    run = one(conn, """
        INSERT INTO second_opinion_runs
          (organization_id, case_id, audit_id, chat_message_id, responder_model_run_id,
           verifier_model_id, verifier_provider, verifier_model_name, status,
           idempotency_key, created_by, cost_source)
        VALUES (:o,:c,:a,:m,:rm,:vm,:vp,:vn,'QUEUED',:k,:u,:cs)
        RETURNING *
    """, o=org_id, c=case_id, a=audit_id, m=message_id, rm=ctx.get("model_run_id"),
          vm=model["id"], vp=model.get("provider"), vn=model.get("model_name"),
          k=idem, u=actor_id, cs=estimate["cost_source"])
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
    out["original_claims"] = rows(conn, """
        SELECT claim_index, claim_text, status
        FROM answer_audit_claims
        WHERE audit_id=:a AND auditor='DETERMINISTIC'
        ORDER BY claim_index
    """, a=str(run["audit_id"])) if run.get("audit_id") else []
    return out


def list_runs(conn: Connection, org_id: str, case_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return rows(conn, """
        SELECT r.*, m.content AS answer_excerpt
        FROM second_opinion_runs r
        LEFT JOIN chat_messages m ON m.id=r.chat_message_id
        WHERE r.organization_id=:o AND r.case_id=:c
        ORDER BY r.created_at DESC LIMIT :l
    """, o=org_id, c=case_id, l=max(1, min(int(limit), 200)))


def case_settings(conn: Connection, org_id: str, case_id: str) -> dict[str, Any]:
    row = one(conn, """
        SELECT second_opinion_enabled, second_opinion_model_id, auto_second_opinion
        FROM case_verification_settings
        WHERE organization_id=:o AND case_id=:c
    """, o=org_id, c=case_id)
    return row or {"second_opinion_enabled": False, "second_opinion_model_id": None,
                   "auto_second_opinion": "NEVER"}


def request_auto(conn: Connection, *, org_id: str, case_id: str, message_id: str,
                 actor_id: str | None, intent: str | None, audit_status: str | None,
                 policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispara la segunda opinión según política, con idempotencia y presupuesto.

    No crea el job: el router lo encola después del commit. Devuelve
    `{"triggered": bool, "reason": str, "run": dict|None}`.
    """
    policy = policy or {}
    settings = get_settings()
    if not settings.SECOND_OPINION_ENABLED:
        return {"triggered": False, "reason": "SECOND_OPINION_DISABLED", "run": None}
    case = case_settings(conn, org_id, case_id)
    required = bool(policy.get("require_second_opinion"))
    reason = auto_should_run(
        case.get("auto_second_opinion"), intent, audit_status,
        str(policy.get("mode") or ""), required=required)
    if reason is None:
        return {"triggered": False, "reason": "POLICY_NEVER", "run": None}
    try:
        run = create_run(conn, org_id=org_id, case_id=case_id, message_id=message_id,
                         actor_id=actor_id, enforce_budget=True)
    except ValueError as exc:
        return {"triggered": False, "reason": str(exc).upper(), "run": None}
    except Exception as exc:  # noqa: BLE001 — el chat no se cae por la segunda opinión
        log.warning("auto second opinion failed: %s", exc)
        return {"triggered": False, "reason": "SECOND_OPINION_ERROR", "run": None}
    return {"triggered": True, "reason": reason, "run": run}


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
                differences.append({"claim_index": oi, "second_index": best[1],
                                    "type": "DIFFERENT_NUMBER", "severity": "HIGH",
                                    "original": sorted(o_nums), "second": sorted(s_nums),
                                    "original_text": oc.get("text"), "second_text": sc.get("text")})
            else:
                differences.append({"claim_index": oi, "second_index": best[1],
                                    "type": "MATCH", "severity": "LOW",
                                    "original_text": oc.get("text"), "second_text": sc.get("text")})
        else:
            differences.append({"claim_index": oi, "type": "MISSING", "severity": "MEDIUM",
                                "original_text": oc.get("text")})
    for si, sc in enumerate(second):
        if si not in used:
            differences.append({"claim_index": len(original) + si, "second_index": si,
                                "type": "EXTRA", "severity": "MEDIUM",
                                "second_text": sc.get("text")})
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
            "original_total": len(original), "second_total": len(second),
            "mode": "DETERMINISTIC"}


def _parse_comparison(raw: str, original: list[dict[str, Any]],
                      second: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Parsea el comparador LLM de forma tolerante; None si no es utilizable."""
    txt = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M).strip()
    data: Any = None
    try:
        data = json.loads(txt)
    except Exception:
        start = txt.find("{")
        if start >= 0:
            try:
                data, _ = json.JSONDecoder().raw_decode(txt[start:])
            except Exception:
                data = None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict") or "").upper()
    if verdict not in {"AGREE", "PARTIAL", "DISAGREE", "INCONCLUSIVE"}:
        return None
    allowed = {"MATCH", "MISSING", "EXTRA", "DIFFERENT_NUMBER", "DIFFERENT_PARTY"}
    differences: list[dict[str, Any]] = []
    matches = 0
    for item in data.get("differences") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").upper()
        if kind not in allowed:
            continue
        try:
            index = int(item.get("claim_index"))
        except (TypeError, ValueError):
            continue
        diff = {"claim_index": index, "type": kind, "reason": str(item.get("reason") or "")[:1000],
                "original_text": item.get("original_text"), "second_text": item.get("second_text")}
        if diff["original_text"] is None and 0 <= index < len(original):
            diff["original_text"] = original[index].get("text")
        second_index = item.get("second_index")
        if not isinstance(second_index, int):
            second_index = index if 0 <= index < len(second) else None
        if diff["second_text"] is None and second_index is not None and 0 <= second_index < len(second):
            diff["second_text"] = second[second_index].get("text")
        diff["second_index"] = second_index
        diff["severity"] = ("HIGH" if kind in {"DIFFERENT_NUMBER", "DIFFERENT_PARTY"}
                            else "LOW" if kind == "MATCH" else "MEDIUM")
        if kind == "MATCH":
            matches += 1
        differences.append(diff)
    if not differences and verdict != "AGREE":
        return None
    return {"verdict": verdict, "differences": differences, "matches": matches,
            "original_total": len(original), "second_total": len(second),
            "mode": "LLM"}


def _merge_comparisons(det: dict[str, Any], judged: dict[str, Any]) -> dict[str, Any]:
    """El comparador LLM interpreta; el determinista impide ocultar cifras u omisiones."""
    differences = list(judged.get("differences") or [])
    keys = {(d.get("claim_index"), d.get("type")) for d in differences}
    for diff in det.get("differences") or []:
        key = (diff.get("claim_index"), diff.get("type"))
        if diff.get("type") in {"DIFFERENT_NUMBER", "MISSING", "EXTRA"} and key not in keys:
            differences.append(diff)
            keys.add(key)
    types = {d.get("type") for d in differences}
    if "DIFFERENT_NUMBER" in types or "DIFFERENT_PARTY" in types:
        verdict = "DISAGREE"
    elif det.get("verdict") == "DISAGREE":
        verdict = "DISAGREE"
    elif "MISSING" in types or "EXTRA" in types or det.get("verdict") == "PARTIAL":
        verdict = "PARTIAL"
    elif judged.get("verdict") in {"AGREE", "PARTIAL", "INCONCLUSIVE"}:
        verdict = judged["verdict"]
    else:
        verdict = det.get("verdict") or "INCONCLUSIVE"
    return {"verdict": verdict, "differences": differences,
            "matches": max(int(det.get("matches") or 0), int(judged.get("matches") or 0)),
            "original_total": det.get("original_total") or len(judged.get("differences") or []),
            "second_total": det.get("second_total") or 0,
            "mode": "LLM"}


def compare_with_llm(original: list[dict[str, Any]], second: list[dict[str, Any]],
                     llm: Any, verifier_name: str = "") -> dict[str, Any]:
    """Comparación semántica con el prompt del plan y guardas deterministas.

    Si el LLM falla o devuelve algo que no se puede parsear, cae al comparador
    determinista sin romper la corrida. Nunca devuelve `AGREE` si el
    determinista detectó una cifra distinta u omisión.
    """
    det = compare(original, second)
    if llm is None:
        det["fallback_reason"] = "llm_unavailable"
        return det
    blocks_o = "\n".join(
        f'<original index="{i}">{str(c.get("text") or "")}</original>'
        for i, c in enumerate(original))
    blocks_s = "\n".join(
        f'<second index="{i}">{str(c.get("text") or "")}</second>'
        for i, c in enumerate(second))
    user = (f"<original_claims>\n{blocks_o}\n</original_claims>\n\n"
            f"<second_claims>\n{blocks_s}\n</second_claims>\n\n"
            "Compara y devuelve SOLO el JSON del esquema. No inventes claims.")
    try:
        result = llm.complete(_load_prompt("second_opinion_compare.v1.md"), user)
    except Exception as exc:  # noqa: BLE001 — fallback determinista
        log.warning("second opinion comparator failed: %s", exc)
        det["fallback_reason"] = f"llm_error:{str(exc)[:200]}"
        det["comparison_model"] = verifier_name
        return det
    judged = _parse_comparison(str(getattr(result, "text", result)), original, second)
    if judged is None:
        det["fallback_reason"] = "invalid_comparison_json"
        det["comparison_model"] = verifier_name
        return det
    merged = _merge_comparisons(det, judged)
    merged["comparison_model"] = verifier_name
    merged["comparison_tokens_in"] = int(getattr(result, "tokens_in", 0) or 0)
    merged["comparison_tokens_out"] = int(getattr(result, "tokens_out", 0) or 0)
    return merged


# Tarifa conservadora SOLO para estimar cuando `ai_models` no tiene precios.
# No es un precio de proveedor: se marca siempre `cost_source='ESTIMATED'`.
_ESTIMATED_INPUT_PER_MTOK = 0.5
_ESTIMATED_OUTPUT_PER_MTOK = 1.5


def cost_for_tokens(model: dict[str, Any] | None, tokens_in: int, tokens_out: int) -> tuple[float, str]:
    """Costo real si el modelo trae precios; si no, estimación explícita."""
    model = model or {}
    try:
        price_in = float(model.get("input_price_per_mtok") or 0)
        price_out = float(model.get("output_price_per_mtok") or 0)
    except (TypeError, ValueError):
        price_in = price_out = 0.0
    if price_in > 0 and price_out > 0:
        cost = (tokens_in / 1_000_000) * price_in + (tokens_out / 1_000_000) * price_out
        return round(cost, 6), "MODEL_PRICE"
    cost = ((tokens_in / 1_000_000) * _ESTIMATED_INPUT_PER_MTOK
            + (tokens_out / 1_000_000) * _ESTIMATED_OUTPUT_PER_MTOK)
    return round(cost, 6), "ESTIMATED"


def _cost(tokens_in: int, tokens_out: int, model: dict[str, Any] | None = None) -> float:
    return cost_for_tokens(model, tokens_in, tokens_out)[0]


def estimate_run_cost(snapshot: list[dict[str, Any]], question: str,
                      model: dict[str, Any]) -> dict[str, Any]:
    """Estimación previa al encolado (no inventa precios)."""
    chars = len(str(question or ""))
    chars += sum(len(str(item.get("text") or "")) for item in snapshot or [])
    tokens_in = max(500, chars // 4 + 800)
    tokens_out = 1800
    cost, source = cost_for_tokens(model, tokens_in, tokens_out)
    return {"tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost": cost, "cost_source": source,
            "limit": float(get_settings().SECOND_OPINION_MAX_COST)}


def auto_should_run(auto_mode: str | None, intent: str | None, audit_status: str | None,
                    guard_mode: str | None = None, required: bool = False) -> str | None:
    """Política pura de disparo automático (idempotencia la pone `create_run`)."""
    if required:
        return "REQUIRED"
    mode = (auto_mode or "NEVER").upper()
    if mode == "ALL":
        return "ALL"
    if mode == "ANALYSIS":
        return "ANALYSIS" if str(intent or "").lower() in {"analisis", "analysis"} else None
    if mode == "STRICT_ONLY":
        if (str(audit_status or "").upper() in {"WARNING", "NEEDS_REVIEW", "BLOCKED"}
                or str(guard_mode or "").upper() == "STRICT"):
            return "STRICT_ONLY"
    return None



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
        comp = compare_with_llm(original_claims, parsed["claims"], llm,
                                str(run.get("verifier_model_name") or ""))
        duration = int((time.monotonic() - started) * 1000)
        tokens_in = int(getattr(result, "tokens_in", 0) or 0) + int(comp.get("comparison_tokens_in") or 0)
        tokens_out = int(getattr(result, "tokens_out", 0) or 0) + int(comp.get("comparison_tokens_out") or 0)
        verifier_model = one(conn, "SELECT * FROM ai_models WHERE id=:i", i=str(run["verifier_model_id"]))
        cost, cost_source = cost_for_tokens(verifier_model, tokens_in, tokens_out)
        validation = {"schema_valid": bool(parsed["claims"]), "verdict": comp["verdict"],
                      "comparison_mode": comp.get("mode")}
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
                cost_source=:cs, comparison_mode=:cm, comparison_error=:ce,
                duration_ms=:dur, completed_at=now()
            WHERE id=:r
        """), {"v": comp["verdict"], "s": _summary(comp),
               "out": json.dumps({"independent": parsed, "comparison": comp},
                                 ensure_ascii=False, default=str),
               "mr": str(model_run["id"]), "ti": tokens_in, "to": tokens_out,
               "cost": cost, "cs": cost_source, "cm": comp.get("mode") or "DETERMINISTIC",
               "ce": comp.get("fallback_reason"), "dur": duration, "r": run_id})
        for i, claim in enumerate(parsed["claims"]):
            diff = next((d for d in comp["differences"]
                         if d.get("second_index") == i or d.get("claim_index") == i), None)
            kind = (diff or {}).get("type")
            comparison = ("CONTRADICTED" if kind in {"DIFFERENT_NUMBER", "DIFFERENT_PARTY", "CONTRADICTED"}
                          else "MATCH" if kind == "MATCH"
                          else "EXTRA" if kind == "EXTRA" else "UNCERTAIN")
            status = ("SUPPORTED" if comparison == "MATCH"
                      else "CONTRADICTED" if comparison == "CONTRADICTED" else "NEEDS_REVIEW")
            conn.execute(text("""
                INSERT INTO second_opinion_claims
                  (organization_id, run_id, claim_index, claim_text, claim_hash, citations,
                   status, comparison, reasons)
                VALUES (:o,:r,:i,:t,:h,CAST(:c AS jsonb),:st,:cmp,CAST(:rs AS jsonb))
                ON CONFLICT (run_id, claim_index) DO NOTHING
            """), {"o": str(run["organization_id"]), "r": run_id, "i": i, "t": claim["text"],
                   "h": _hash(claim["text"]), "c": json.dumps(claim.get("citations") or [], ensure_ascii=False),
                   "st": status, "cmp": comparison,
                   "rs": json.dumps(diff or {}, ensure_ascii=False, default=str)})
        for diff in comp["differences"]:
            if diff.get("type") in {"DIFFERENT_NUMBER", "DIFFERENT_PARTY", "MISSING", "EXTRA"}:
                conn.execute(text("""
                    INSERT INTO second_opinion_findings
                      (organization_id, run_id, claim_index, code, severity, message, metadata)
                    VALUES (:o,:r,:i,:code,:sev,:msg,CAST(:m AS jsonb))
                """), {"o": str(run["organization_id"]), "r": run_id, "i": diff.get("claim_index"),
                       "code": f"MODEL_{diff['type']}", "sev": diff.get("severity", "MEDIUM"),
                       "msg": (f"El segundo modelo {diff.get('type','')} en el claim "
                               f"{diff.get('claim_index')}: {str(diff.get('reason') or '')[:300]}"),
                       "m": json.dumps(diff, ensure_ascii=False, default=str)})
        return {"run_id": run_id, "status": "COMPLETED", "verdict": comp["verdict"],
                "comparison_mode": comp.get("mode"), "cost_source": cost_source}
    except Exception as exc:  # noqa: BLE001
        log.exception("second opinion run failed")
        conn.execute(text("""
            UPDATE second_opinion_runs SET status='FAILED', error_code='SECOND_OPINION_ERROR',
              error_detail=:e, completed_at=now() WHERE id=:r
        """), {"e": str(exc)[:1000], "r": run_id})
        return {"run_id": run_id, "status": "FAILED", "error": str(exc)}


def _summary(comp: dict[str, Any]) -> str:
    mode = f" Comparación {comp.get('mode', 'DETERMINISTIC').lower()}."
    fallback = (f" Fallback: {comp['fallback_reason']}." if comp.get("fallback_reason") else "")
    return (f"{comp.get('verdict')}: {comp.get('matches')} coincidencias de "
            f"{comp.get('original_total')} claims originales y {comp.get('second_total')} del verificador."
            + mode + fallback)
