"""Verificación semántica anti-alucinación (SSD §33).

Complementa el grounding léxico/cifras de answering.py con un juez semántico
(LLM) que clasifica cada claim de la respuesta frente a la evidencia citada.

Por defecto el juez corre FUERA de la ruta crítica (`VERIFY_MODE=async`): el
turno responde ya y el veredicto se guarda en `model_runs.validation`. Todo el
módulo es defensivo: un juez caído nunca rompe la respuesta.
"""
from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

from sqlalchemy import text as sql_text

from app.core.config import get_settings
from app.core.db import tx
from app.providers.llm import get_llm, get_llm_for_task
from app.services import text_terms

log = logging.getLogger(__name__)

_UNSUPPORTED = {"not_supported", "contradicted"}


def _source_label(it: dict[str, Any]) -> str:
    if it.get("source_type") == "document_page" or it.get("document_id"):
        return f"document:{it.get('document_id')} page:{it.get('page_number')}"
    if it.get("source_type") == "transcript_segment" or it.get("media_id"):
        return f"media:{it.get('media_id')} {it.get('start_ms')}-{it.get('end_ms')}ms"
    return f"node:{it.get('node_id')}"


def _build_prompt(claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    blocks = []
    for it in evidence:
        blocks.append(f'<evidence id="{it["handle"]}" source="{html.escape(_source_label(it), quote=True)}">\n'
                      f'{html.escape(str(it.get("text") or ""), quote=False)}\n</evidence>')
    claim_blocks = []
    for cl in claims:
        claim_blocks.append(f'<claim citations="{",".join(cl.get("citations", []))}">{html.escape(cl["text"], quote=False)}</claim>')
    return "<claims>\n" + "\n".join(claim_blocks) + "\n</claims>\n\n" + "\n".join(blocks)


def _judge_llm(llm=None):
    """Juez configurado para `verification` o el proveedor por defecto."""
    if llm is not None:
        return llm
    try:
        routing = get_settings().LLM_ROUTING or {}
        if "verification" in routing:
            return get_llm_for_task("verification")
    except Exception as exc:  # noqa: BLE001
        log.debug("verification routing unavailable: %s", exc)
    return get_llm()


def _system_prompt() -> str:
    s = get_settings()
    path = s.path(s.PROMPTS_DIR)
    target = path / "verify_answer.v2.md"
    if not target.exists():
        target = path / "verify_answer.v1.md"
    return (target.read_text(encoding="utf-8").split("---", 2)[2].strip()
            .replace("{{LOCALE}}", "es"))


def verify(claims: list[dict[str, Any]], evidence: list[dict[str, Any]], llm=None) -> list[dict[str, Any]]:
    """Devuelve la lista de veredictos extendida con las citas originales."""
    if not claims:
        return []
    try:
        system = _system_prompt()
        # El juez solo debe ver las fuentes que la respuesta citará. Si ve todo
        # el expediente, puede aprobar una afirmación con una página no citada
        # (p. ej. un número que aparece en otro documento).
        cited = {str(handle) for claim in claims
                 for handle in (claim.get("citations") or [])}
        visible = [item for item in evidence if str(item.get("handle")) in cited]
        user = _build_prompt(claims, visible or evidence)
        result = _judge_llm(llm).complete(system, user)
    except Exception as exc:  # noqa: BLE001 — el juez es opcional
        log.warning("verification failed: %s", exc)
        return []
    txt = re.sub(r"^```(?:json)?|```$", "", str(getattr(result, "text", result)).strip(), flags=re.M).strip()
    try:
        data = json.loads(txt)
        verdicts = data.get("verdicts") or [] if isinstance(data, dict) else []
    except Exception:
        return []
    if not isinstance(verdicts, list):
        return []
    by_text = {text_terms.norm(cl["text"]): cl.get("citations", []) for cl in claims}
    out: list[dict[str, Any]] = []
    for verdict in verdicts:
        if not isinstance(verdict, dict):
            continue
        claim_text = verdict.get("claim", "")
        verdict["citations"] = by_text.get(text_terms.norm(claim_text),
                                          verdict.get("citations", []))
        status = verdict.get("status")
        if status not in ("supported", "not_supported", "contradicted", "needs_review"):
            verdict["status"] = "needs_review"
        out.append(verdict)
    return out


def unsupported_verdicts(verdicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [v for v in verdicts or [] if v.get("status") in _UNSUPPORTED]


def apply_verdicts(validated: dict[str, Any], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Quita de la respuesta lo que el juez no respalda; nunca lo deja como hecho.

    Un veredicto `needs_review` tampoco se publica como afirmación: se mueve a
    límites para que un humano decida. El match es por texto normalizado porque
    el juez a veces devuelve la afirmación truncada.
    """
    unsupported = list(validated.get("unsupported_claims") or [])
    known = {text_terms.norm(str(item.get("text") or "")) for item in unsupported}
    uncertainties = list(validated.get("uncertainties") or [])
    claims = list(validated.get("claims") or [])
    by_norm = {text_terms.norm(str(c.get("text") or "")): c for c in claims}
    for verdict in verdicts or []:
        claim = str(verdict.get("claim") or "")
        status = verdict.get("status")
        folded = text_terms.norm(claim)
        target = by_norm.get(folded)
        if target is None and folded:
            for candidate in claims:
                candidate_norm = text_terms.norm(str(candidate.get("text") or ""))
                if candidate_norm and (candidate_norm[:80] in folded
                                       or folded[:80] in candidate_norm):
                    target = candidate
                    break
        if status in _UNSUPPORTED:
            if claim and folded not in known:
                known.add(folded)
                unsupported.append({"text": claim, "citations": verdict.get("citations", []),
                                    "reason": status, "semantic_reason": verdict.get("reason", "")})
            if target is not None:
                claims = [c for c in claims if c is not target]
        elif status == "needs_review":
            if target is not None:
                claims = [c for c in claims if c is not target]
            note = f"Revisión humana sugerida: {claim} ({verdict.get('reason', '')})"
            if note not in uncertainties:
                uncertainties.append(note)
    validated["claims"] = claims
    validated["unsupported_claims"] = unsupported
    validated["uncertainties"] = uncertainties
    return validated


def verify_and_store(org_id: str, user_id: str | None, model_run_id: str,
                     claims: list[dict[str, Any]], evidence: list[dict[str, Any]],
                     llm=None) -> list[dict[str, Any]]:
    """Verifica y guarda el veredicto en `model_runs.validation`. Nunca lanza."""
    try:
        verdicts = verify(claims, evidence, llm=llm)
        if not verdicts:
            return []
        payload = {"semantic_verdicts": verdicts,
                   "semantic_unsupported": len(unsupported_verdicts(verdicts))}
        with tx(org_id, user_id) as conn:
            conn.execute(sql_text("""
                UPDATE model_runs
                SET validation = coalesce(validation, '{}'::jsonb) || CAST(:v AS jsonb)
                WHERE id = :id
            """), {"v": json.dumps(payload, ensure_ascii=False, default=str), "id": str(model_run_id)})
        return verdicts
    except Exception as exc:  # noqa: BLE001 — post-respuesta: nunca rompe el turno
        log.warning("verify_and_store failed: %s", exc)
        return []
