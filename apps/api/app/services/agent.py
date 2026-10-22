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
from app.services import agent_tools, answering, case_tools, evidence_labels, skills, verification

log = logging.getLogger(__name__)
MAX_STEPS = 5


def _read_prompt(versioned: str, fallback: str) -> str:
    """Carga `versioned` si existe; si no, la versión anterior (contrato §1.13)."""
    s = get_settings()
    path = s.path(s.PROMPTS_DIR)
    raw_path = path / versioned
    if not raw_path.exists():
        raw_path = path / fallback
    return raw_path.read_text(encoding="utf-8").split("---", 2)[2].strip()


def _history_text(turns: list[dict[str, Any]]) -> str:
    """Historial con presupuesto de caracteres, conservando los turnos más recientes."""
    max_chars = get_settings().CHAT_HISTORY_MAX_CHARS
    lines: list[str] = []
    used = 0
    for turn in reversed(turns):
        content = str(turn.get("content", ""))
        # Un turno nunca puede comerse todo el presupuesto.
        content = content[: max(200, max_chars // 3)]
        role = "USER" if turn.get("role") == "user" else "ASSISTANT"
        line = f"{role}: {content}"
        if lines and used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(reversed(lines))


def _system_prompt(agent_prompt: str | None = None,
                   parameters: dict[str, Any] | None = None,
                   deep: bool = False) -> str:
    base = _read_prompt("agent_system.v8.md", "agent_system.v7.md")
    # El system prompt del agente configurado (+ sus skills) se antepone al contrato de tools/formato.
    parts = [agent_prompt.strip()] if agent_prompt else []
    if parameters:
        lines = "\n".join(f"- {key}: {value}" for key, value in parameters.items())
        parts.append(f"<skill_parameters>\n{html.escape(lines, quote=False)}\n</skill_parameters>")
    if deep:
        parts.append(
            "<deep_analysis>\n"
            "This turn asks for an exhaustive analysis (all contradictions, a cross-check, or a complete "
            "inventory). Do NOT stop after the first search. Run several distinct searches with "
            "search_case using k=20: by party, by person, by document, by date and by topic. Use the graph "
            "and claims tools when they help. Cover every relevant fragment, including audio/video "
            "testimony, and only then write the final synthesis. If the evidence is related but does not "
            "fully answer, return a detailed partial analysis instead of refusing.\n"
            "</deep_analysis>"
        )
    parts.append(base)
    return "\n\n".join(parts)


def _emit(emit, event: str, data: dict | None = None) -> None:
    """Un emisor defectuoso jamás debe romper el turno."""
    if emit is None:
        return
    try:
        emit(event, data or {})
    except Exception:  # noqa: BLE001
        log.debug("emit %s failed", event, exc_info=True)


def load_agent_prompt(conn: Connection, agent_id: str) -> str | None:
    """System prompt efectivo del agente: el suyo + el de cada skill enlazada.

    Se mantiene por compatibilidad; el orden y los allowlists viven en
    `skills.load_config`."""
    return skills.load_config(conn, agent_id)["prompt"]


def load_agent_config(conn: Connection, agent_id: str | None) -> dict[str, Any]:
    """Config efectiva (prompt, allowed_tools, parameters) del agente."""
    return skills.load_config(conn, agent_id)



def _parse_turn(raw: str) -> dict[str, Any]:
    txt = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        data = json.loads(txt)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        log.debug("failed to parse agent turn: %s", exc)
    return {}


def _format_tool_result(tool: str, result: list[dict[str, Any]],
                        max_chars: int | None = None) -> str:
    """Resultado de tool delimitado y escapado (anti prompt-injection).

    El texto del expediente es datos, no instrucciones: cualquier `<tool_result>`
    o etiqueta de control que venga en el OCR/ASR se escapa y queda dentro de
    un único bloque delimitado."""
    lines = [f"RESULT of {html.escape(str(tool), quote=False)}:"]
    for it in result:
        handle = html.escape(str(it.get("handle", "")), quote=False)
        source = html.escape(str(it.get("source_type", "")), quote=False)
        hint = html.escape(_evidence_hint(it, max_chars=max_chars), quote=False)
        lines.append(f"- {handle} ({source}): {hint}")
    return "<tool_result>\n" + "\n".join(lines) + "\n</tool_result>"


def _carried_evidence_block(items: list[dict[str, Any]]) -> str:
    """Páginas/segmentos ya citados: el agente los ve antes de buscar de nuevo."""
    max_chars = get_settings().AGENT_TOOL_RESULT_MAX_CHARS
    lines: list[str] = []
    for item in items[:8]:
        handle = html.escape(str(item.get("handle") or ""), quote=False)
        if not handle:
            continue
        label = html.escape(evidence_labels.one(item), quote=False)
        hint = html.escape(_evidence_hint(item, max_chars=max_chars), quote=False)
        lines.append(f"- {handle} ({label}): {hint}")
    if not lines:
        return ""
    return (
        "<carried_evidence>\n"
        "Estas son las fuentes exactas que se citaron en la respuesta anterior. "
        "Úsalas para resolver el seguimiento y cita su handle. No las ignores.\n"
        + "\n".join(lines) + "\n</carried_evidence>\n"
    )


_TOOL_VERBS = {
    "search_case": "Buscando en el expediente",
    "read_document": "Leyendo el documento",
    "get_document_page": "Leyendo la página",
    "find_person": "Buscando a la persona y sus relaciones",
    "graph_query": "Consultando el grafo del caso",
    "search_transcript_by_time": "Revisando la transcripción",
    "get_claims": "Revisando las afirmaciones registradas",
    "get_file": "Abriendo el archivo",
}


def _tool_label(tool: str, arguments: dict[str, Any]) -> str:
    verb = _TOOL_VERBS.get(tool, f"Consultando {tool}")
    query = (arguments.get("query") or arguments.get("term") or arguments.get("name")
             or arguments.get("q"))
    if tool in ("read_document", "get_document_page"):
        name = arguments.get("document_id") or arguments.get("filename")
        page = arguments.get("page_number") or arguments.get("page")
        if name:
            query = str(name)
            if page is not None:
                query += f" · p. {page}"
    if query:
        text = " ".join(str(query).split())[:110]
        return f"{verb}: «{text}»…"
    return f"{verb}…"


def _tool_done_label(tool: str, items: list[dict[str, Any]]) -> str:
    primary = [it for it in items if it.get("source_type") in ("document_page", "transcript_segment")]
    count = len(primary)
    noun = "fragmento" if count == 1 else "fragmentos"
    sources = evidence_labels.many(primary, limit=2)
    if count:
        label = f"Encontré {count} {noun}"
        if sources:
            label += ": " + " · ".join(sources)
        return label
    return "No encontré fragmentos citables en esta búsqueda"


def _evidence_hint(it: dict[str, Any], max_chars: int | None = None) -> str:
    """Describe una evidencia de forma que el modelo vea minuto/hablante/página/filename.
    Sin esto, el modelo no sabía en qué minuto ni quién lo dijo aunque la cita sí lo mostraba."""
    max_chars = max_chars or get_settings().AGENT_TOOL_RESULT_MAX_CHARS
    text = str(it.get("text", ""))[:max_chars]
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
                    hints: list[str] | None = None,
                    seed_evidence: list[dict[str, Any]] | None = None,
                    ocr_mode: str | None = None, asr_mode: str | None = None,
                    emit=None, allowed_tools: set[str] | None = None,
                    verify: bool = True, max_steps: int = MAX_STEPS,
                    parameters: dict[str, Any] | None = None,
                    deep: bool = False) -> dict[str, Any]:
    """Ejecuta el agente: tool loop → síntesis → (opcional) verificación semántica.

    `agent_prompt` permite que un agente configurado en el panel dirija la consulta
    (su system prompt + skills se anteponen al contrato de herramientas y formato).
    `llm` permite forzar un proveedor/modelo distinto al configurado por defecto.
    `attachments` son los "@" reales del chat [{kind, id, name}]: se declaran al
    agente para que lea esos archivos con las tools.
    `org_id`/`actor_id` alimentan el ToolContext de las tools de escritura
    (correcciones con reviews + auditoría).
    `history_turns` (chat multi-turn): últimos K turnos [{role, content}] que dan
    contexto a follow-ups ("¿y quién más estaba?").
    `emit` recibe eventos de etapa/tool sin poder romper el turno.
    `allowed_tools` es el allowlist efectivo de las skills (None = sin restricción).
    `max_steps` acota el bucle ReAct (ANALYSIS usa 3 desde el chat).
    """
    agent_tools.reset_handles()
    ctx = case_tools.ToolContext(org_id=org_id, actor_id=actor_id, locale=locale,
                                 attachments=attachments or [],
                                 ocr_mode=ocr_mode, asr_mode=asr_mode)
    case_tools.set_context(ctx)
    tool_calls: list[dict[str, Any]] = []
    degraded = False
    # NOTA: las tools de escritura difieren la propagación (pgvector/grafo/jobs) en
    # ctx.post_commit; se devuelve en el resultado ("post_commit") y el llamador la
    # ejecuta con case_tools.drain_actions() DESPUÉS de confirmar su transacción.
    try:
        system = _system_prompt(agent_prompt, parameters, deep=deep)
        history = ""
        if hints:
            block = "\n".join(f"- {h}" for h in hints)
            history += f"<context_hints>\n{html.escape(block, quote=False)}\n</context_hints>\n"
        if history_turns:
            convo = _history_text(history_turns)
            history += (f"<conversation_history>\n{html.escape(convo, quote=False)}\n"
                        "</conversation_history>\nThe conversation above is context; "
                        "answer the NEW question below.\n")
        history += f"<question>\n{html.escape(question, quote=False)}\n</question>\n"
        if seed_evidence:
            carried = _carried_evidence_block(seed_evidence)
            if carried:
                history += carried
        if attachments:
            lines = "\n".join(f"- {a.get('kind')}: {a.get('name') or ''} (id: {a.get('id')})" for a in attachments)
            history += (f"<attached_files>\n{html.escape(lines, quote=False)}\n</attached_files>\n"
                        "The user attached the files above with @; prefer reading them directly "
                        "(read_document / get_file / search_case with their ids).\n")
        evidence: list[dict[str, Any]] = list(seed_evidence or [])
        corrections_pending: list[dict[str, Any]] = []
        corrections_done: list[dict[str, Any]] = []
        llm = llm or get_llm()

        steps = max(1, int(max_steps or MAX_STEPS))
        steps_exhausted = False
        tool_errors = 0
        for step in range(steps):
            user = history + "\nWhat do you do next?"
            try:
                result = llm.complete(system, user)
            except Exception as exc:  # noqa: BLE001 — proveedor caído: degradar
                log.warning("agent llm failed: %s", exc)
                degraded = True
                break
            turn = _parse_turn(result.text)
            log.debug("agent step %s turn=%s", step, turn)
            if not turn:
                degraded = True
                break
            if turn.get("done"):
                break
            tool = turn.get("tool")
            args = turn.get("arguments") or {}
            if not tool or not isinstance(args, dict):
                degraded = True
                break
            allowed, reason = skills.validate_tool(str(tool), allowed_tools)
            if not allowed:
                result_items = [{"handle": "ERR", "source_type": "error", "text": reason or "tool not allowed"}]
                tool_errors += 1
                tool_calls.append({"name": str(tool), "arguments": args, "allowed": False})
            else:
                tool_calls.append({"name": str(tool), "arguments": args, "allowed": True})
                _emit(emit, "tool", {
                    "name": str(tool), "step": step + 1, "phase": "start",
                    "arguments": args, "label": _tool_label(str(tool), args),
                })
                try:
                    result_items = agent_tools.execute(conn, case_id, tool, args)
                except Exception as exc:  # noqa: BLE001
                    result_items = [{"handle": "ERR", "source_type": "error",
                                     "text": f"Error ejecutando {tool}: {exc}"}]
                    tool_errors += 1
                _emit(emit, "tool", {
                    "name": str(tool), "step": step + 1, "phase": "done",
                    "evidence": len([it for it in result_items
                                     if it.get("source_type") in
                                     ("document_page", "transcript_segment")]),
                    "sources": evidence_labels.many(result_items, limit=2),
                    "label": _tool_done_label(str(tool), result_items),
                })
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
            history += _format_tool_result(
                tool, result_items,
                max_chars=(get_settings().AGENT_TOOL_RESULT_MAX_CHARS * 3 if deep else None)) + "\n"
        else:
            # Se agotaron los pasos sin un turno `done`: el bucle no terminó, pero
            # puede haber evidencia suficiente. Solo se degrada si la síntesis
            # tampoco produce claims (más abajo).
            steps_exhausted = True

        evidence = _dedupe_evidence(evidence)
        # Tarjetas de archivo (get_file/list_case_files): no son evidencia citable;
        # viajan aparte para que la UI pinte botones de ver/descargar.
        file_cards = _file_cards(evidence)
        # La respuesta final solo puede citar fuentes primarias (documentos/páginas o segmentos de audio/video)
        primary_evidence = [it for it in evidence if it.get("source_type") in ("document_page", "transcript_segment")]
        if not primary_evidence:
            return {"answer": t("messages.insufficient_evidence", locale), "claims": [], "citations": [],
                    "unsupported_claims": [], "uncertainties": [t("messages.insufficient_evidence", locale)],
                    "tool_calls": tool_calls, "degraded": degraded,
                    "file_cards": file_cards, "post_commit": list(ctx.post_commit),
                    "corrections_pending": corrections_pending, "corrections_done": corrections_done,
                    "evidence_count": 0}
        evidence = primary_evidence

        _emit(emit, "reviewing", {
            "label": "Revisando la evidencia que devolvieron las herramientas…",
            "evidence": len(evidence),
        })
        # Síntesis final con el prompt de respuesta RAG existente (v3 en modo profundo)
        system, prompt_id, prompt_version = answering.system_prompt(locale, deep=deep)
        user = answering.build_user_prompt(question, evidence, history=history_turns, deep=deep)
        _emit(emit, "synthesizing", {
            "label": f"Redactando la respuesta con {len(evidence)} fragmentos…",
            "evidence": len(evidence),
            "sources": evidence_labels.many(evidence),
        })
        try:
            result = llm.complete(system, user)
        except Exception as exc:  # noqa: BLE001
            log.warning("agent synthesis failed: %s", exc)
            return {"answer": t("messages.insufficient_evidence", locale), "claims": [], "citations": [],
                    "unsupported_claims": [], "uncertainties": [], "schema_valid": False,
                    "raw_text": "", "prompt_id": "", "prompt_version": "",
                    "provider": getattr(llm, "name", ""), "model": getattr(llm, "model", ""),
                    "tokens_in": 0, "tokens_out": 0, "evidence": evidence, "file_cards": file_cards,
                    "post_commit": list(ctx.post_commit), "corrections_pending": corrections_pending,
                    "corrections_done": corrections_done, "tool_calls": tool_calls,
                    "degraded": True, "evidence_count": len(evidence)}
        validated = answering.parse_and_validate(result.text, evidence, get_settings().ANSWER_MIN_GROUNDING_OVERLAP)
        # Un seguimiento no puede quedarse sin respuesta si ya existen páginas
        # citadas en el turno anterior: se reintenta la síntesis SOLO con esas
        # fuentes, sin volver a buscar ni inventar hechos.
        if not validated["claims"] and seed_evidence:
            carried = [it for it in seed_evidence
                       if it.get("handle") and it.get("source_type") in
                       ("document_page", "transcript_segment")]
            if carried:
                _emit(emit, "reviewing", {
                    "label": "Revisando las fuentes citadas en la respuesta anterior…",
                    "evidence": len(carried),
                    "sources": evidence_labels.many(carried),
                })
                try:
                    retry_system, retry_pid, retry_ver = answering.system_prompt(locale, deep=False)
                    retry_prompt = answering.build_user_prompt(
                        question, carried, history=history_turns, deep=False)
                    retry_prompt += (
                        "\n\n<retry>La síntesis anterior no produjo afirmaciones citables. "
                        "Responde únicamente con las fuentes de <evidence>. Si un fragmento "
                        "contiene la declaración, cítalo con su handle; si solo responde en "
                        "parte, di exactamente qué falta.</retry>")
                    retry_result = llm.complete(retry_system, retry_prompt)
                    retry_validated = answering.parse_and_validate(
                        retry_result.text, carried, get_settings().ANSWER_MIN_GROUNDING_OVERLAP)
                    if retry_validated["claims"]:
                        validated = retry_validated
                        result = retry_result
                        prompt_id, prompt_version = retry_pid, retry_ver
                except Exception as exc:  # noqa: BLE001 — si falla, se mantiene la abstención
                    log.warning("carryover synthesis failed: %s", exc)
        if (steps_exhausted or tool_errors) and not validated["claims"]:
            degraded = True

        # Verificación semántica LLM-juez sobre claims con citación válida.
        # El router puede desactivarla para aplicarla él mismo (sync/async/off).
        semantic: list[dict[str, Any]] = []
        if verify:
            try:
                semantic = verification.verify(validated["claims"], evidence)
            except Exception as exc:  # noqa: BLE001 — nunca romper el turno
                log.warning("semantic verification failed: %s", exc)
                semantic = []
        unsupported = list(validated["unsupported_claims"])
        for v in semantic:
            if v.get("status") in ("not_supported", "contradicted"):
                # Mover a unsupported
                unsupported.append({"text": v.get("claim", ""), "citations": v.get("citations", []),
                                    "reason": v.get("status"), "semantic_reason": v.get("reason", "")})
            elif v.get("status") == "needs_review":
                validated["uncertainties"].append(f"Revisión humana sugerida: {v.get('claim', '')} ({v.get('reason', '')})")

        if validated["claims"]:
            # Un fallo de tool/parseo no invalida una síntesis con claims citables.
            degraded = False
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
            "tool_calls": tool_calls,
            "degraded": degraded,
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
