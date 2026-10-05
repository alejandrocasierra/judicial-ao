"""Chat multi-turn por expediente (Fase 3): sesiones persistentes, historial
para el agente (~últimos K turnos), adjuntos "@" reales y file_cards.

El pipeline de respuesta ES el de /query (mismo presupuesto de tokens, rate
limits, grounding anti-alucinación y verificación semántica): aquí solo se
añade la memoria de conversación y la persistencia de los mensajes.
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.core.i18n import negotiate, t
from app.schemas import ChatMessageIn, ChatSessionIn, QueryIn
from app.security.deps import Principal, case_access, current_principal
from app.services import audit, case_tools, correction, query_hints, ratelimit
from app.services.case_tools import ToolContext
from app.services.case_tools.read import _content_term
from app.routers.query import _attachment_file_cards, _run_query

router = APIRouter(prefix="/cases/{case_id}/chats", tags=["chat"])

_ADMIN_ROLES = {"ORG_ADMIN", "CASE_MANAGER"}
log = logging.getLogger(__name__)


def _load_pending(p: Principal, case_id: UUID, session_id: UUID) -> dict | None:
    with tx(p.org_id, p.user_id) as c:
        return one(c, """SELECT id, tool, arguments, summary FROM chat_pending_corrections
            WHERE session_id = :s AND resolved_at IS NULL ORDER BY created_at DESC LIMIT 1""",
                   s=str(session_id))


def _store_pending(p: Principal, case_id: UUID, session_id: UUID, pendings: list[dict]) -> None:
    """Guarda la propuesta del agente (tool + argumentos exactos) para la confirmación."""
    with tx(p.org_id, p.user_id) as c:
        # Solo una pendiente por sesión: las anteriores sin resolver se descartan.
        c.execute(text("UPDATE chat_pending_corrections SET resolved_at = now(), resolved_reason = 'superseded' "
                       "WHERE session_id = :s AND resolved_at IS NULL"), {"s": str(session_id)})
        for item in pendings:
            c.execute(text("""INSERT INTO chat_pending_corrections
                (organization_id, session_id, tool, arguments, summary)
                VALUES (:o, :s, :tool, CAST(:args AS jsonb), :summary)"""),
                {"o": p.org_id, "s": str(session_id), "tool": item["tool"],
                 "args": json.dumps(item.get("arguments", {})), "summary": item.get("summary", "")[:2000]})


def _resolve_pending(p: Principal, pending_id: str, reason: str) -> None:
    with tx(p.org_id, p.user_id) as c:
        c.execute(text("UPDATE chat_pending_corrections SET resolved_at = now(), resolved_reason = :r WHERE id = :i"),
                  {"r": reason, "i": str(pending_id)})


def _clear_pending(p: Principal, case_id: UUID, session_id: UUID) -> None:
    with tx(p.org_id, p.user_id) as c:
        c.execute(text("UPDATE chat_pending_corrections SET resolved_at = now(), resolved_reason = 'applied' "
                       "WHERE session_id = :s AND resolved_at IS NULL"), {"s": str(session_id)})


def _apply_pending(p: Principal, case_id: UUID, pending: dict) -> dict:
    """Ejecuta la corrección pendiente con confirm=true (determinista) y propaga."""
    ctx = ToolContext(org_id=p.org_id, actor_id=p.user_id)
    args = dict(pending["arguments"] or {})
    args["confirm"] = True
    with tx(p.org_id, p.user_id) as c:
        items = case_tools.execute(c, str(case_id), pending["tool"], args, ctx=ctx)
    case_tools.drain_post_commit(ctx)
    _resolve_pending(p, str(pending["id"]), "applied")
    ok = any(it.get("source_type") == "correction_result" for it in items)
    detail = next((it.get("text", "") for it in items if it.get("source_type") == "correction_result"), "")
    error = next((it.get("text", "") for it in items if it.get("source_type") == "error"), "")
    if ok:
        return {"answer": detail or "", "applied": True}
    return {"answer": error or detail or "", "applied": False}


def _finish_confirmation(s, p: Principal, case_id: UUID, session_id: UUID, user_msg: dict,
                         answer: str, request: Request, extra: dict | None = None,
                         citations: list | None = None, file_cards: list | None = None) -> dict:
    """Persiste el turno de una respuesta determinista (confirmación/cancelación/localización)."""
    result = {"answer": answer, "claims": [], "citations": citations or [], "unsupported_claims": [],
              "uncertainties": [], "file_cards": file_cards or [], "pending_correction": None, **(extra or {})}
    with tx(p.org_id, p.user_id) as c:
        assistant_msg = one(c, """INSERT INTO chat_messages (organization_id, session_id, role, content,
                                     attachments, citations, model_run_id)
            VALUES (:o, :s, 'assistant', :t, CAST(:fc AS jsonb), CAST(:ci AS jsonb), NULL) RETURNING id, created_at""",
                            o=p.org_id, s=str(session_id), t=answer,
                            fc=json.dumps(file_cards or []), ci=json.dumps(citations or []))
        c.execute(text("UPDATE chat_sessions SET updated_at = now() WHERE id = :s"), {"s": str(session_id)})
    return {"user_message": user_msg, "assistant_message": assistant_msg, **result}


def _locate_locations(p: Principal, case_id: UUID, term: str) -> list[dict]:
    ctx = ToolContext(org_id=p.org_id, actor_id=p.user_id)
    with tx(p.org_id, p.user_id) as c:
        return case_tools.execute(c, str(case_id), "locate", {"term": term, "k": 300}, ctx=ctx)


def _format_locations(term: str, locs: list[dict], locale: str) -> tuple[str, list[dict]]:
    """Enumera cada archivo+página (documentos) y archivo+minuto+hablante (audiencias)."""
    from app.services.case_tools.read import mmss
    if not locs:
        return t("messages.locate_none", locale).format(term=term), []
    lines: list[str] = []
    cites: list[dict] = []
    seen: set[str] = set()
    for i, it in enumerate(locs):
        if it.get("source_type") == "document_page":
            folio = f" (folio {it['folio']})" if it.get("folio") else ""
            line = f"- {it.get('filename')} · página {it.get('page_number')}{folio}"
            cite = {"citation_id": f"loc{i}", "source_type": "document_page", "filename": it.get("filename"),
                    "document_id": it.get("document_id"), "page": it.get("page_number"), "folio": it.get("folio")}
        else:
            spk = f" · {it['speaker']}" if it.get("speaker") else ""
            line = f"- {it.get('filename')} · {mmss(it.get('start_ms'))}{spk}"
            cite = {"citation_id": f"loc{i}", "source_type": "transcript_segment", "filename": it.get("filename"),
                    "media_id": it.get("media_id"), "start_ms": it.get("start_ms"), "end_ms": it.get("end_ms"),
                    "speaker": it.get("speaker")}
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        cites.append(cite)
    answer = t("messages.locate_intro", locale).format(term=term) + "\n" + "\n".join(lines)
    return answer, cites


def _session(conn, case_id: UUID, session_id: UUID) -> dict:
    s = one(conn, "SELECT * FROM chat_sessions WHERE id = :s AND case_id = :c AND archived_at IS NULL",
            s=str(session_id), c=str(case_id))
    if not s:
        raise AppError("CHAT_NOT_FOUND", 404)
    return s


@router.get("")
def list_sessions(case_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        return rows(c, """SELECT s.id, s.title, s.agent_id, s.model_id, s.created_by, s.created_at, s.updated_at,
                (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
            FROM chat_sessions s WHERE s.case_id = :c AND s.archived_at IS NULL
            ORDER BY s.updated_at DESC""", c=str(case_id))


@router.post("", status_code=201)
def create_session(case_id: UUID, body: ChatSessionIn, request: Request, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        if body.agent_id:
            a = one(c, "SELECT id FROM agents WHERE id = :i", i=str(body.agent_id))
            if not a:
                raise AppError("AGENT_NOT_FOUND", 404)
        if body.model_id:
            m = one(c, "SELECT id FROM ai_models WHERE id = :i", i=str(body.model_id))
            if not m:
                raise AppError("NOT_FOUND", 404)
        s = one(c, """INSERT INTO chat_sessions (organization_id, case_id, title, agent_id, model_id, created_by)
            VALUES (:o, :c, :t, :a, :m, :u) RETURNING id, title, agent_id, model_id, created_at""",
                o=p.org_id, c=str(case_id), t=body.title or "", a=str(body.agent_id) if body.agent_id else None,
                m=str(body.model_id) if body.model_id else None, u=p.user_id)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="chat.session_created",
                     entity_type="chat_session", entity_id=str(s["id"]),
                     after={"agent_id": str(body.agent_id) if body.agent_id else None}, request=request)
    return s


@router.get("/{session_id}/messages")
def list_messages(case_id: UUID, session_id: UUID, limit: int = 50, offset: int = 0,
                  p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    limit = min(max(1, limit), 200)
    offset = max(0, offset)
    with tx(p.org_id, p.user_id) as c:
        _session(c, case_id, session_id)
        msgs = rows(c, """SELECT id, role, content, attachments, citations, occurrences, model_run_id, created_at
            FROM chat_messages WHERE session_id = :s ORDER BY created_at, id LIMIT :l OFFSET :o""",
                    s=str(session_id), l=limit, o=offset)
    return {"messages": msgs, "limit": limit, "offset": offset}


@router.post("/{session_id}/messages", status_code=201)
def send_message(case_id: UUID, session_id: UUID, body: ChatMessageIn, request: Request,
                 p: Principal = Depends(current_principal)):
    s = get_settings()
    case = case_access(p, case_id, "ai.query")
    ratelimit.check("query", p.user_id)
    ratelimit.check_org("query", p.org_id)
    if case["spent_llm_tokens"] >= case["max_llm_tokens"]:
        raise AppError("BUDGET_EXCEEDED", 409)
    locale = negotiate(request.headers.get("accept-language"), p.locale)

    with tx(p.org_id, p.user_id) as c:
        session = _session(c, case_id, session_id)
        history = rows(c, """SELECT role, content FROM chat_messages WHERE session_id = :s
            ORDER BY created_at DESC, id DESC LIMIT :k""", s=str(session_id), k=s.CHAT_HISTORY_TURNS)
        history.reverse()
        user_msg = one(c, """INSERT INTO chat_messages (organization_id, session_id, role, content, attachments)
            VALUES (:o, :s, 'user', :t, CAST(:a AS jsonb)) RETURNING id, created_at""",
                       o=p.org_id, s=str(session_id), t=body.content,
                       a=json.dumps([{"kind": a.kind, "id": str(a.id), "name": a.name} for a in body.attachments]))

    # Flujo de corrección DETERMINISTA (Fase 6): si el usuario confirma o cancela y
    # hay una corrección pendiente en esta sesión, se aplica/descarta aquí mismo,
    # sin depender de que el LLM reconstruya la propuesta en este turno.
    if correction.is_confirmation(body.content) or correction.is_cancellation(body.content):
        pending = _load_pending(p, case_id, session_id)
        if pending:
            if correction.is_cancellation(body.content):
                _resolve_pending(p, pending["id"], "cancelled")
                return _finish_confirmation(s, p, case_id, session_id, user_msg,
                                            answer=t("messages.correction_cancelled", locale), request=request,
                                            extra={"pending_correction": None})
            applied = _apply_pending(p, case_id, pending)
            return _finish_confirmation(s, p, case_id, session_id, user_msg, answer=applied["answer"],
                                        request=request,
                                        extra={"corrections_done": [{"tool": pending["tool"],
                                                                     "arguments": pending["arguments"]}],
                                               "corrections_pending": [], "pending_correction": None})

    # Pista determinista al agente cuando el mensaje huele a corrección o a calidad OCR/ASR.
    if len(body.content.strip()) < 3:
        # Respuesta corta ("sí", "ok") sin corrección pendiente: no hay nada que el agente
        # pueda hacer con una pregunta tan corta; se responde con honestidad.
        return _finish_confirmation(s, p, case_id, session_id, user_msg,
                                    answer=t("messages.correction_none", locale), request=request)
    # Pista general (todas las fuentes) + corrección de OCR/ASR.
    hints = query_hints.merge(correction.hint(body.content, bool(body.attachments)),
                              query_hints.hint(body.content))
    query_body = QueryIn(question=body.content, strategy=body.strategy,
                         agent_id=session["agent_id"], model_id=session["model_id"],
                         attachments=body.attachments)
    result = _run_query(case_id, query_body, request, p, locale, case, s, history=history, hints=hints)

    # Los "@" siempre producen tarjeta ver/descargar, aunque el agente no llamara get_file.
    if body.attachments:
        with tx(p.org_id, p.user_id) as c:
            attached_cards = _attachment_file_cards(c, case_id, query_body)
        known = {card.get("document_id") or card.get("media_id") for card in result.get("file_cards", [])}
        result["file_cards"] = result.get("file_cards", []) + [
            card for card in attached_cards if (card.get("document_id") or card.get("media_id")) not in known]

    # "Aparece en": TODAS las ubicaciones del término (documentos en ambos modos de OCR y
    # audiencias), sin límite arbitrario por archivo, para citar cada página/minuto.
    term = _content_term(body.content)
    if term:
        try:
            with tx(p.org_id, p.user_id) as c:
                occ = case_tools.execute(c, str(case_id), "locate", {"term": term, "k": 300},
                                         ctx=ToolContext(org_id=p.org_id, actor_id=p.user_id))
            _, occ_cites = _format_locations(term, occ, locale)
            if occ_cites:
                result["occurrences"] = occ_cites
        except Exception:  # noqa: BLE001
            log.debug("no se pudieron calcular las apariciones del término", exc_info=True)

    # El agente propuso correcciones: se guardan para la confirmación del usuario.
    pendings = result.get("corrections_pending") or []
    if pendings:
        _store_pending(p, case_id, session_id, pendings)
        result["pending_correction"] = pendings[0]
    elif result.get("corrections_done"):
        _clear_pending(p, case_id, session_id)

    with tx(p.org_id, p.user_id) as c:
        assistant_msg = one(c, """INSERT INTO chat_messages (organization_id, session_id, role, content,
                                     attachments, citations, occurrences, model_run_id)
            VALUES (:o, :s, 'assistant', :t, CAST(:fc AS jsonb), CAST(:ci AS jsonb), CAST(:oc AS jsonb), :mr)
            RETURNING id, created_at""",
                            o=p.org_id, s=str(session_id), t=result["answer"],
                            fc=json.dumps(result.get("file_cards", [])),
                            ci=json.dumps(result.get("citations", [])),
                            oc=json.dumps(result.get("occurrences", [])),
                            mr=result.get("model_run_id"))
        # Título automático de la primera pregunta y actividad de la sesión.
        title = session["title"] or body.content[:80]
        c.execute(text("UPDATE chat_sessions SET title = :t, updated_at = now() WHERE id = :s"),
                  {"t": title, "s": str(session_id)})
    return {"user_message": user_msg, "assistant_message": assistant_msg, **result}


@router.delete("/{session_id}")
def archive_session(case_id: UUID, session_id: UUID, request: Request, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        session = _session(c, case_id, session_id)
        # Solo el creador de la sesión o un administrador de la org puede archivarla.
        if str(session["created_by"]) != str(p.user_id) and p.org_role not in _ADMIN_ROLES:
            raise AppError("FORBIDDEN", 403)
        one(c, "UPDATE chat_sessions SET archived_at = now() WHERE id = :s RETURNING id", s=str(session_id))
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="chat.session_archived",
                     entity_type="chat_session", entity_id=str(session_id), request=request)
    return {"archived": True}
