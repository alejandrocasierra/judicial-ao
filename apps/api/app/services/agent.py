"""Agente jurídico con tools allowlist y verificación anti-alucinación (Fase 7)."""
from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.i18n import t
from app.providers.llm import get_llm
from app.services import agent_tools, answering, case_tools, verification

log = logging.getLogger(__name__)
MAX_STEPS = 5


def _system_prompt(agent_prompt: str | None = None) -> str:
    s = get_settings()
    raw = (s.path(s.PROMPTS_DIR) / "agent_system.v6.md").read_text(encoding="utf-8")
    base = raw.split("---", 2)[2].strip()
    # El system prompt del agente configurado (+ sus skills) se antepone al contrato de tools/formato.
    return f"{agent_prompt.strip()}\n\n{base}" if agent_prompt else base


def load_agent_prompt(conn: Connection, agent_id: str) -> str | None:
    """System prompt efectivo del agente: el suyo + el de cada skill enlazada.

    Antes las skills eran data muerta (se guardaban ids pero nunca se leían);
    aquí se fusionan al prompt en el orden en que fueron enlazadas."""
    from app.core.db import one, rows
    a = one(conn, "SELECT system_prompt, skills FROM agents WHERE id = :i", i=agent_id)
    if not a:
        return None
    parts = [a["system_prompt"].strip()] if a["system_prompt"] and a["system_prompt"].strip() else []
    skill_ids = a.get("skills") or []
    if skill_ids:
        marks = ",".join(f":s{i}" for i in range(len(skill_ids)))
        params = {f"s{i}": sid for i, sid in enumerate(skill_ids)}
        prompts = rows(conn, "SELECT name, system_prompt FROM skills WHERE id IN (/*MARKS*/)".replace("/*MARKS*/", marks), **params)
        parts += [f"[Skill: {p['name']}]\n{p['system_prompt'].strip()}"
                  for p in prompts if p["system_prompt"] and p["system_prompt"].strip()]
    return "\n\n".join(parts) or None


def _parse_turn(raw: str) -> dict[str, Any]:
    txt = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        data = json.loads(txt)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        log.debug("failed to parse agent turn: %s", exc)
    return {}


def _format_tool_result(tool: str, result: list[dict[str, Any]]) -> str:
    lines = [f"RESULT of {tool}:"]
    for it in result:
        lines.append(f'- {it["handle"]} ({it.get("source_type")}): {_evidence_hint(it)}')
    return "\n".join(lines)


def _evidence_hint(it: dict[str, Any]) -> str:
    """Describe una evidencia de forma que el modelo vea minuto/hablante/página/filename.
    Sin esto, el modelo no sabía en qué minuto ni quién lo dijo aunque la cita sí lo mostraba."""
    text = str(it.get("text", ""))[:400]
    person = ""
    if it.get("person_name"):
        person = f" · {it.get('role') or 'rol'}: {it['person_name']} ({it.get('mentions')} menciones)"
    if it.get("source_type") == "transcript_segment":
        filename = it.get("filename") or "video"
        minute = it.get("start_mmss") or _mmss(it.get("start_ms"))
        speaker = it.get("speaker") or "sin identificar"
        return f"[{filename} · minuto {minute or '?'} · hablante: {speaker}] {text}"
    if it.get("source_type") == "document_page":
        filename = it.get("filename") or "documento"
        folio = f" · folio {it.get('folio')}" if it.get("folio") else ""
        return f"[{filename} · página {it.get('page_number')}{folio}{person}] {text}"
    return text


def _mmss(ms: Any) -> str:
    try:
        total = int(ms) // 1000
        return f"{total // 60:02d}:{total % 60:02d}"
    except (TypeError, ValueError):
        return ""


def _dedupe_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for it in evidence:
        key = str(it.get("document_id") or it.get("segment_id") or it.get("node_id") or it.get("claim_id") or it.get("fact_id") or it.get("evidence_id")) + ":" + str(it.get("handle"))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def run_agent_query(conn: Connection, case_id: str, question: str, locale: str,
                    agent_prompt: str | None = None, llm=None,
                    attachments: list[dict[str, Any]] | None = None,
                    org_id: str | None = None, actor_id: str | None = None,
                    history_turns: list[dict[str, Any]] | None = None,
                    hints: list[str] | None = None) -> dict[str, Any]:
    """Ejecuta el agente: tool loop → síntesis → verificación semántica.

    `agent_prompt` permite que un agente configurado en el panel dirija la consulta
    (su system prompt + skills se anteponen al contrato de herramientas y formato).
    `llm` permite forzar un proveedor/modelo distinto al configurado por defecto.
    `attachments` son los "@" reales del chat [{kind, id, name}]: se declaran al
    agente para que lea esos archivos con las tools.
    `org_id`/`actor_id` alimentan el ToolContext de las tools de escritura
    (correcciones con reviews + auditoría).
    `history_turns` (chat multi-turn): últimos K turnos [{role, content}] que dan
    contexto a follow-ups ("¿y quién más estaba?").
    """
    agent_tools.reset_handles()
    ctx = case_tools.ToolContext(org_id=org_id, actor_id=actor_id, locale=locale,
                                 attachments=attachments or [])
    case_tools.set_context(ctx)
    # NOTA: las tools de escritura difieren la propagación (pgvector/grafo/jobs) en
    # ctx.post_commit; se devuelve en el resultado ("post_commit") y el llamador la
    # ejecuta con case_tools.drain_actions() DESPUÉS de confirmar su transacción.
    try:
        system = _system_prompt(agent_prompt)
        history = ""
        if hints:
            block = "\n".join(f"- {h}" for h in hints)
            history += f"<context_hints>\n{html.escape(block, quote=False)}\n</context_hints>\n"
        if history_turns:
            convo = "\n".join(f"{'USER' if t.get('role') == 'user' else 'ASSISTANT'}: "
                              f"{str(t.get('content', ''))[:800]}" for t in history_turns)
            history += (f"<conversation_history>\n{html.escape(convo, quote=False)}\n"
                        "</conversation_history>\nThe conversation above is context; "
                        "answer the NEW question below.\n")
        history += f"<question>\n{html.escape(question, quote=False)}\n</question>\n"
        if attachments:
            lines = "\n".join(f"- {a.get('kind')}: {a.get('name') or ''} (id: {a.get('id')})" for a in attachments)
            history += (f"<attached_files>\n{html.escape(lines, quote=False)}\n</attached_files>\n"
                        "The user attached the files above with @; prefer reading them directly "
                        "(read_document / get_file / search_case with their ids).\n")
        evidence: list[dict[str, Any]] = []
        corrections_pending: list[dict[str, Any]] = []
        corrections_done: list[dict[str, Any]] = []
        llm = llm or get_llm()

        for step in range(MAX_STEPS):
            user = history + "\nWhat do you do next?"
            result = llm.complete(system, user)
            turn = _parse_turn(result.text)
            log.debug("agent step %s turn=%s", step, turn)
            if not turn:
                break
            if turn.get("done"):
                break
            tool = turn.get("tool")
            args = turn.get("arguments") or {}
            if not tool or not isinstance(args, dict):
                break
            try:
                result_items = agent_tools.execute(conn, case_id, tool, args)
            except Exception as exc:
                result_items = [{"handle": "ERR", "source_type": "error", "text": f"Error ejecutando {tool}: {exc}"}]
            # Correcciones: guardamos tool+argumentos exactos para que el servidor
            # pueda aplicarlas de forma determinista cuando el usuario confirme.
            for it in result_items:
                if it.get("source_type") == "correction_preview":
                    corrections_pending.append({"tool": tool, "arguments": args,
                                                "summary": it.get("text", ""),
                                                "requires_confirmation": True})
                elif it.get("source_type") == "correction_result":
                    corrections_done.append({"tool": tool, "arguments": args, "text": it.get("text", "")})
            evidence.extend(result_items)
            history += f"\nACTION: {tool}({json.dumps(args, ensure_ascii=False)})\n"
            history += _format_tool_result(tool, result_items) + "\n"

        evidence = _dedupe_evidence(evidence)
        # Tarjetas de archivo (get_file/list_case_files): no son evidencia citable;
        # viajan aparte para que la UI pinte botones de ver/descargar.
        file_cards = _file_cards(evidence)
        # La respuesta final solo puede citar fuentes primarias (documentos/páginas o segmentos de audio/video)
        primary_evidence = [it for it in evidence if it.get("source_type") in ("document_page", "transcript_segment")]
        if not primary_evidence:
            return {"answer": t("messages.insufficient_evidence", locale), "claims": [], "citations": [],
                    "unsupported_claims": [], "uncertainties": [t("messages.insufficient_evidence", locale)],
                    "tool_calls": [], "file_cards": file_cards, "post_commit": list(ctx.post_commit),
                    "corrections_pending": corrections_pending, "corrections_done": corrections_done,
                    "evidence_count": 0}
        evidence = primary_evidence

        # Síntesis final con el prompt de respuesta RAG existente
        system, prompt_id, prompt_version = answering.system_prompt(locale)
        user = answering.build_user_prompt(question, evidence)
        result = llm.complete(system, user)
        validated = answering.parse_and_validate(result.text, evidence, get_settings().ANSWER_MIN_GROUNDING_OVERLAP)

        # Verificación semántica LLM-juez sobre claims con citación válida
        semantic = verification.verify(validated["claims"], evidence)
        unsupported = list(validated["unsupported_claims"])
        for v in semantic:
            if v["status"] in ("not_supported", "contradicted"):
                # Mover a unsupported
                unsupported.append({"text": v["claim"], "citations": v.get("citations", []),
                                    "reason": v["status"], "semantic_reason": v.get("reason", "")})
            elif v["status"] == "needs_review":
                validated["uncertainties"].append(f"Revisión humana sugerida: {v['claim']} ({v.get('reason', '')})")

        return {
            "claims": validated["claims"],
            "unsupported_claims": unsupported,
            "uncertainties": validated["uncertainties"],
            "schema_valid": validated["schema_valid"],
            "raw_text": result.text,
            "prompt_id": prompt_id,
            "prompt_version": prompt_version,
            "provider": result.provider,
            "model": result.model,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "evidence": evidence,
            "file_cards": file_cards,
            "post_commit": list(ctx.post_commit),
            "evidence_count": len(evidence),
        }
    finally:
        case_tools.set_context(None)


def _file_cards(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ítems source_type='file' → tarjetas para la UI (ver/descargar), deduplicadas por id."""
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in evidence:
        if it.get("source_type") != "file":
            continue
        fid = it.get("document_id") or it.get("media_id")
        if not fid or fid in seen:
            continue
        seen.add(fid)
        cards.append({"kind": it.get("kind"), "name": it.get("name") or it.get("filename"),
                      "document_id": it.get("document_id"), "media_id": it.get("media_id"),
                      "mime_type": it.get("mime_type"), "size_bytes": it.get("size_bytes"),
                      "page_count": it.get("page_count"), "duration_ms": it.get("duration_ms"),
                      "download_path": it.get("download_path"), "view_path": it.get("view_path")})
    return cards
