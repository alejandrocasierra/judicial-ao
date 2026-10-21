"""Chat multi-turn por expediente: router de intención, memoria y streaming.

`strategy="auto"` decide la tubería según la intención:

- `agregacion` → lista determinista (`role_answer`), 0 llamadas al LLM;
- `navegar` → `locate` + `occurrences`, 0 llamadas;
- `procedimental` → `process_path` como evidencia y una síntesis;
- `analisis` → agente acotado (3 pasos) + síntesis;
- `dato`/`lectura` → RAG con una sola síntesis.

`strategy="agent"`/`"rag"` conservan el camino histórico. El formato final lo
decide `compose.render`; aquí solo se orquesta y se persiste.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.core.i18n import negotiate, t
from app.schemas import ChatMessageIn, ChatSessionIn, IntentFeedbackIn, QueryIn
from app.security.deps import Principal, case_access, current_principal
from app.services import (audit, case_tools, claim_seed, contradiction_seed, correction,
                          deadline as deadline_mod, entity_seed, intent as intent_service,
                          intent_profile, memory, occurrences, party_answer, query_hints,
                          ratelimit, role_answer, text_terms)
from app.services.case_tools import ToolContext
from app.routers.query import _attachment_file_cards, _run_query, _safe_emit

router = APIRouter(prefix="/cases/{case_id}/chats", tags=["chat"])

_ADMIN_ROLES = {"ORG_ADMIN", "CASE_MANAGER"}
log = logging.getLogger(__name__)


def _is_fresh(value, seconds: int) -> bool:
    """True si `value` (timestamptz de psycopg) está dentro de la ventana."""
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - value).total_seconds() <= seconds


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
    args = dict(pending["arguments"] or {})
    args["confirm"] = True
    needed = case_tools.required_permission(str(pending["tool"]), args)
    case_access(p, case_id, needed)
    ctx = ToolContext(org_id=p.org_id, actor_id=p.user_id, allow_write=True,
                      permissions={needed})
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
                         citations: list | None = None, file_cards: list | None = None,
                         answer_kind: str = "correction", intent: str = "correccion",
                         degraded: bool = False, intent_confidence: float = 1.0,
                         intent_reason: str = "confirmation_flow",
                         intent_source: str = "deterministic") -> dict:
    """Persiste el turno de una respuesta determinista (confirmación/cancelación/corrección)."""
    result = {"answer": answer, "answer_kind": answer_kind, "intent": intent, "degraded": degraded,
              "claims": [], "citations": citations or [], "citations_used": [], "unsupported_claims": [],
              "uncertainties": [], "file_cards": file_cards or [], "pending_correction": None,
              "occurrences": [], "occurrences_term": None, "occurrences_note": None, **(extra or {})}
    with tx(p.org_id, p.user_id) as c:
        assistant_msg = one(c, """INSERT INTO chat_messages
                (organization_id, session_id, role, content, attachments, citations, occurrences,
                 uncertainties, occurrences_term, occurrences_note, answer_kind, intent, degraded, model_run_id,
                 intent_confidence, intent_reason, intent_source, router_version)
            VALUES (:o, :s, 'assistant', :t, CAST(:fc AS jsonb), CAST(:ci AS jsonb), CAST(:oc AS jsonb),
                    CAST(:unc AS jsonb), :ot, :on, :ak, :intent, :dg, NULL, :ic, :ir, :isrc, :rv)
            RETURNING id, created_at""",
                            o=p.org_id, s=str(session_id), t=answer,
                            fc=json.dumps(file_cards or []), ci=json.dumps(citations or []),
                            oc=json.dumps(result.get("occurrences") or []),
                            unc=json.dumps(result.get("uncertainties") or []),
                            ot=result.get("occurrences_term"), on=result.get("occurrences_note"),
                            ak=answer_kind, intent=intent, dg=degraded,
                            ic=intent_confidence, ir=intent_reason, isrc=intent_source,
                            rv=intent_service.ROUTER_VERSION)
        c.execute(text("UPDATE chat_sessions SET updated_at = now() WHERE id = :s"), {"s": str(session_id)})
    return {"user_message": user_msg, "assistant_message": assistant_msg, **result}


def _session(conn, case_id: UUID, session_id: UUID) -> dict:
    s = one(conn, "SELECT * FROM chat_sessions WHERE id = :s AND case_id = :c AND archived_at IS NULL",
            s=str(session_id), c=str(case_id))
    if not s:
        raise AppError("CHAT_NOT_FOUND", 404)
    return s


def _locate_locations(p: Principal, case_id: UUID, term: str,
                      ocr_mode: str | None = None, asr_mode: str | None = None) -> list[dict]:
    ctx = ToolContext(org_id=p.org_id, actor_id=p.user_id, ocr_mode=ocr_mode, asr_mode=asr_mode)
    with tx(p.org_id, p.user_id) as c:
        return case_tools.execute(c, str(case_id), "locate", {"term": term, "k": 300}, ctx=ctx)


def _process_path_seed(p: Principal, case_id: UUID, question: str,
                       ocr_mode: str | None = None, asr_mode: str | None = None) -> list[dict]:
    """Antecedentes procesales de la pregunta; [] si el grafo no la resuelve."""
    ctx = ToolContext(org_id=p.org_id, actor_id=p.user_id, ocr_mode=ocr_mode, asr_mode=asr_mode)
    try:
        with tx(p.org_id, p.user_id) as c:
            items = case_tools.execute(c, str(case_id), "process_path",
                                       {"event": question, "direction": "to"}, ctx=ctx)
    except Exception:  # noqa: BLE001 — sin grafo se cae al RAG normal
        log.debug("process_path failed", exc_info=True)
        return []
    return [it for it in items or [] if it.get("source_type") != "error"]


def _citation_from_item(item: dict, marker: int) -> dict | None:
    if item.get("document_id") and item.get("page_number") is not None:
        return {"citation_id": f"det{marker}", "marker": marker, "source_type": "document_page",
                "document_id": str(item["document_id"]), "page": item.get("page_number"),
                "folio": item.get("folio"), "filename": item.get("filename")}
    if item.get("media_id") and item.get("segment_id") and item.get("start_ms") is not None:
        return {"citation_id": f"det{marker}", "marker": marker, "source_type": "transcript_segment",
                "media_id": str(item["media_id"]), "segment_id": str(item["segment_id"]),
                "start_ms": item.get("start_ms"), "end_ms": item.get("end_ms"),
                "speaker": item.get("speaker"), "filename": item.get("filename")}
    return None


def _persist_deterministic(p: Principal, case_id: UUID, items: list[dict], *, task: str,
                           output: dict) -> tuple[str | None, list[dict]]:
    """model_run + citations reales para respuestas sin LLM (rol/navegación)."""
    with tx(p.org_id, p.user_id) as c:
        run = one(c, """INSERT INTO model_runs (organization_id, case_id, task, provider, model, prompt_id,
              prompt_version, pipeline_version, input_hash, output, validation, tokens_in, tokens_out, actor_id)
            VALUES (:o,:c,:task,'deterministic','deterministic','deterministic','1',:plv,:ih,
                    CAST(:out AS jsonb),'{}'::jsonb,0,0,:u) RETURNING id""",
                  o=p.org_id, c=str(case_id), task=task, plv=get_settings().PIPELINE_VERSION,
                  ih=hashlib.sha256(f"{task}:{case_id}:{output.get('items')}".encode()).hexdigest(),
                  out=json.dumps(output, ensure_ascii=False, default=str),
                  u=p.user_id)
        citations: list[dict] = []
        marker = 0
        seen: set[tuple] = set()
        for item in items:
            locate_key = (str(item.get("document_id")), item.get("page_number")) \
                if item.get("document_id") else (str(item.get("segment_id")),)
            if locate_key in seen:
                continue
            cite = _citation_from_item(item, marker + 1)
            if not cite:
                continue
            seen.add(locate_key)
            if cite["source_type"] == "document_page":
                cid = one(c, """INSERT INTO citations (organization_id, case_id, target_type, target_id,
                        source_type, document_id, page_number, folio)
                    VALUES (:o,:c,'answer',:r,'document_page',:d,:pg,:f) RETURNING id""",
                          o=p.org_id, c=str(case_id), r=run["id"], d=cite["document_id"],
                          pg=cite["page"], f=cite.get("folio"))["id"]
            else:
                cid = one(c, """INSERT INTO citations (organization_id, case_id, target_type, target_id,
                        source_type, media_id, segment_id, start_ms, end_ms)
                    VALUES (:o,:c,'answer',:r,'transcript_segment',:m,:sg,:s,:e) RETURNING id""",
                          o=p.org_id, c=str(case_id), r=run["id"], m=cite["media_id"],
                          sg=cite["segment_id"], s=cite["start_ms"], e=cite["end_ms"])["id"]
            marker += 1
            cite["citation_id"] = str(cid)
            citations.append(cite)
            if marker >= 10:
                break
    return str(run["id"]), citations


def _aggregation_result(p: Principal, case_id: UUID, content: str, locale: str) -> dict | None:
    # Las partes del proceso viven en `parties` con su rol canónico. Para una
    # pregunta de rol/partes, esa ficha es la fuente; las menciones sueltas de
    # páginas producían listas falsas («Sujetos Procesales» como demandado).
    with tx(p.org_id, p.user_id) as c:
        party_text = party_answer.render_for_question(c, str(case_id), content)
    if party_text:
        run_id, citations = _persist_deterministic(
            p, case_id, [], task="party_answer",
            output={"answer_kind": "direct", "intent": "agregacion",
                    "source": "parties"})
        return {"answer": party_text, "answer_kind": "direct", "intent": "agregacion",
                "degraded": False, "claims": [], "citations": [], "citations_used": [],
                "unsupported_claims": [], "uncertainties": [], "file_cards": [],
                "model_run_id": run_id, "evidence_count": 0,
                "occurrences": [], "occurrences_term": None, "occurrences_note": None}
    role = query_hints.role_from_text(content)
    if not role:
        return None
    items = role_answer.authoritative_evidence(str(p.org_id), str(p.user_id), str(case_id), content)
    answer = role_answer.render(role, items)
    if not answer:
        return None
    run_id, citations = _persist_deterministic(p, case_id, items, task="role_answer",
                                               output={"answer_kind": "direct", "intent": "agregacion",
                                                       "role": role, "items": len(items)})
    return {"answer": answer, "answer_kind": "direct", "intent": "agregacion", "degraded": False,
            "claims": [], "citations": citations, "citations_used": [c["citation_id"] for c in citations],
            "unsupported_claims": [], "uncertainties": [], "file_cards": [],
            "model_run_id": run_id, "evidence_count": len(items),
            "occurrences": [], "occurrences_term": None, "occurrences_note": None}


def _navigate_result(p: Principal, case_id: UUID, content: str, decision, locale: str,
                     ocr_mode: str | None, asr_mode: str | None, s) -> dict:
    term = getattr(decision, "nav_term", None) or text_terms.specific_term(content) \
        or query_hints.locate_term(content)
    if not term or not occurrences.is_informative(term):
        answer = t("messages.locate_none", locale).format(term="")
        return {"answer": answer, "answer_kind": "navigational", "intent": "navegar", "degraded": False,
                "claims": [], "citations": [], "citations_used": [], "unsupported_claims": [],
                "uncertainties": [], "file_cards": [], "evidence_count": 0,
                "occurrences": [], "occurrences_term": None, "occurrences_note": None}
    locs = _locate_locations(p, case_id, term, ocr_mode, asr_mode)
    text, cites = occurrences.build(term, locs, locale, max_items=s.NAV_MAX_OCCURRENCES)
    note = t("messages.occurrences_note", locale).format(term=term)
    # La UI agrupa y pinta `occurrences`; no duplicamos la lista completa en el
    # Markdown (evita el muro de timestamps del incidente original).
    answer = note if cites else text
    return {"answer": answer, "answer_kind": "navigational", "intent": "navegar", "degraded": False,
            "claims": [], "citations": [], "citations_used": [], "unsupported_claims": [],
            "uncertainties": [], "file_cards": [], "evidence_count": len(locs),
            "occurrences": cites, "occurrences_term": term, "occurrences_note": note}


def _update_summary(s, p: Principal, session_id: UUID, session: dict) -> None:
    """Resume los turnos anteriores a los últimos K (memoria de la sesión)."""
    turns = int(s.CHAT_HISTORY_TURNS)
    with tx(p.org_id, p.user_id) as c:
        total = c.execute(text("SELECT count(*) FROM chat_messages WHERE session_id = :s"),
                          {"s": str(session_id)}).scalar() or 0
        if total <= turns:
            return
        history = rows(c, """SELECT role, content FROM chat_messages WHERE session_id = :s
            ORDER BY created_at ASC, id ASC LIMIT 200""", s=str(session_id))
    older = history[:-turns] if len(history) > turns else []
    if not older:
        return
    llm = memory.summary_llm() if len(older) > turns * 2 else None
    fresh = memory.summarize(older, llm=llm)
    merged = memory.merge_summary(session.get("summary"), fresh)
    with tx(p.org_id, p.user_id) as c:
        c.execute(text("UPDATE chat_sessions SET summary = :sum, summary_upto = now() WHERE id = :s"),
                  {"sum": merged, "s": str(session_id)})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _chunks(text: str, size: int = 24):
    for start in range(0, len(text), size):
        yield text[start:start + size]


@router.get("")
def list_sessions(case_id: UUID, p: Principal = Depends(current_principal)):
    case_access(p, case_id, "case.read")
    with tx(p.org_id, p.user_id) as c:
        return rows(c, """SELECT s.id, s.title, s.agent_id, s.model_id, s.created_by, s.created_at, s.updated_at,
                s.ocr_mode, s.asr_mode, s.summary,
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
        s = one(c, """INSERT INTO chat_sessions (organization_id, case_id, title, agent_id, model_id,
                            created_by, ocr_mode, asr_mode)
            VALUES (:o, :c, :t, :a, :m, :u, :ocr, :asr) RETURNING id, title, agent_id, model_id,
                     created_at, ocr_mode, asr_mode""",
                o=p.org_id, c=str(case_id), t=body.title or "", a=str(body.agent_id) if body.agent_id else None,
                m=str(body.model_id) if body.model_id else None, u=p.user_id,
                ocr=body.ocr_mode, asr=body.asr_mode)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="chat.session_created",
                     entity_type="chat_session", entity_id=str(s["id"]),
                     after={"agent_id": str(body.agent_id) if body.agent_id else None,
                            "ocr_mode": body.ocr_mode, "asr_mode": body.asr_mode}, request=request)
    return s


@router.get("/{session_id}/messages")
def list_messages(case_id: UUID, session_id: UUID, limit: int = 50, offset: int = 0,
                  p: Principal = Depends(current_principal)):
    """Paginación de los MÁS RECIENTES: offset=0 devuelve el final de la conversación."""
    case_access(p, case_id, "case.read")
    limit = min(max(1, limit), 200)
    offset = max(0, offset)
    with tx(p.org_id, p.user_id) as c:
        _session(c, case_id, session_id)
        msgs = rows(c, """SELECT page.*, f.verdict, f.corrected_intent
            FROM (
                SELECT id, role, content, attachments, citations, occurrences, uncertainties,
                       occurrences_term, occurrences_note, answer_kind, intent,
                       intent_confidence, intent_reason, intent_source, router_version,
                       degraded, model_run_id, created_at
                FROM chat_messages WHERE session_id = :s
                ORDER BY created_at DESC, id DESC LIMIT :l OFFSET :o
            ) page
            LEFT JOIN intent_feedback f ON f.message_id = page.id AND f.user_id = :u
            ORDER BY page.created_at ASC, page.id ASC""",
                   s=str(session_id), l=limit, o=offset, u=p.user_id)
    return {"messages": msgs, "limit": limit, "offset": offset}


@router.post("/{session_id}/messages/{message_id}/feedback", status_code=201)
def send_feedback(case_id: UUID, session_id: UUID, message_id: UUID, body: IntentFeedbackIn,
                  p: Principal = Depends(current_principal)):
    """Veredicto del usuario sobre un turno: la única etiqueta VERDADERA del router.

    `wrong_intent` trae la intención correcta; `accepted`/`rejected` son un clic.
    Un veredicto por usuario y mensaje (upsert): cambiar de opinión actualiza el
    anterior en vez de acumular filas. Es una llamada aparte de la del turno, así
    que nunca afecta al deadline ni a la respuesta ya emitida.
    """
    case_access(p, case_id, "case.read")
    corrected = body.corrected_intent if body.verdict == "wrong_intent" else None
    with tx(p.org_id, p.user_id) as c:
        msg = one(c, """SELECT m.intent, m.router_version
                          FROM chat_messages m
                          JOIN chat_sessions s ON s.id = m.session_id
                         WHERE m.id = :m AND m.session_id = :s AND s.case_id = :c
                           AND m.role = 'assistant'""",
                  m=str(message_id), s=str(session_id), c=str(case_id))
        if not msg:
            raise AppError("CHAT_NOT_FOUND", 404)
        saved = one(c, """INSERT INTO intent_feedback
                (organization_id, case_id, session_id, message_id, user_id,
                 verdict, corrected_intent, note, router_intent, router_version)
            VALUES (:o, :c, :s, :m, :u, :v, :ci, :n, :ri, :rv)
            ON CONFLICT (message_id, user_id) DO UPDATE
               SET verdict = EXCLUDED.verdict,
                   corrected_intent = EXCLUDED.corrected_intent,
                   note = EXCLUDED.note,
                   router_intent = EXCLUDED.router_intent,
                   router_version = EXCLUDED.router_version
            RETURNING id, verdict, corrected_intent, updated_at""",
                         o=p.org_id, c=str(case_id), s=str(session_id), m=str(message_id),
                         u=p.user_id, v=body.verdict, ci=corrected, n=body.note,
                         ri=msg.get("intent"), rv=msg.get("router_version"))
    return saved


def _prepare_turn(case_id: UUID, session_id: UUID, body: ChatMessageIn, p: Principal):
    """Carga sesión/historial, resuelve agente/modelo e inserta el mensaje del usuario."""
    s = get_settings()
    with tx(p.org_id, p.user_id) as c:
        session = _session(c, case_id, session_id)
        history = rows(c, """SELECT role, content, intent FROM chat_messages WHERE session_id = :s
            ORDER BY created_at DESC, id DESC LIMIT :k""", s=str(session_id), k=s.CHAT_HISTORY_TURNS)
        history.reverse()
        # Memoria entre sesiones del MISMO usuario y expediente: los últimos
        # 2 intercambios de sesiones anteriores dan continuidad a «lo anterior»
        # sin arrastrar todo el historial ni gastar tokens en él.
        previous = rows(c, """SELECT m.role, m.content FROM chat_messages m
                                JOIN chat_sessions cs ON cs.id = m.session_id
                               WHERE cs.case_id = :c AND cs.created_by = :u
                                 AND cs.id <> :sid AND cs.archived_at IS NULL
                               ORDER BY m.created_at DESC, m.id DESC LIMIT 4""",
                       c=str(case_id), u=str(p.user_id), sid=str(session_id))
        previous.reverse()
        history = ([{"role": str(turn.get("role") or ""),
                     "content": str(turn.get("content") or "")[:800]}
                    for turn in previous] + history)
        # Cuando la conversación supera la ventana de turnos, el resumen sigue
        # aportando el tema para que un seguimiento no se quede sin contexto.
        if session.get("summary"):
            history.insert(0, {
                "role": "user",
                "content": "Resumen de la conversación previa:\n" + str(session["summary"]),
            })
        attachments_json = json.dumps([{"kind": a.kind, "id": str(a.id), "name": a.name}
                                       for a in body.attachments])
        # Idempotencia del fallback SSE: si el último mensaje es el MISMO usuario
        # (misma sesión, mismo texto/adjuntos, sin respuesta aún y <2 min), se
        # reutiliza en vez de duplicarlo. El stream puede insertarlo y fallar
        # después; el POST de respaldo no debe crear un segundo turno.
        last = one(c, """SELECT id, role, content, attachments, created_at
            FROM chat_messages WHERE session_id = :s ORDER BY created_at DESC, id DESC LIMIT 1""",
                   s=str(session_id))
        reuse = (last and last.get("role") == "user" and last.get("content") == body.content
                 and (last.get("attachments") or []) == json.loads(attachments_json)
                 and _is_fresh(last.get("created_at"), seconds=120))
        if reuse:
            user_msg = {"id": last["id"], "created_at": last["created_at"]}
        else:
            user_msg = one(c, """INSERT INTO chat_messages
                    (organization_id, session_id, role, content, attachments, user_id)
                VALUES (:o, :s, 'user', :t, CAST(:a AS jsonb), :u)
                RETURNING id, created_at""",
                           o=p.org_id, s=str(session_id), t=body.content, a=attachments_json,
                           u=p.user_id)
        ocr_mode = body.ocr_mode if body.ocr_mode is not None else session.get("ocr_mode")
        asr_mode = body.asr_mode if body.asr_mode is not None else session.get("asr_mode")
        # El agente/modelo del mensaje tiene prioridad; si cambian, la sesión los recuerda.
        agent_id = body.agent_id if body.agent_id is not None else session.get("agent_id")
        model_id = body.model_id if body.model_id is not None else session.get("model_id")
        if model_id is None:
            # Sesiones antiguas sin modelo: usar el default de la organización en
            # vez de caer al LLM global (que en dev es `fake`).
            default_model = one(c, """SELECT id FROM ai_models
                                       WHERE organization_id = :o AND is_default = true
                                       ORDER BY created_at LIMIT 1""", o=p.org_id)
            model_id = default_model["id"] if default_model else None
        session_changed = (str(agent_id or "") != str(session.get("agent_id") or "")
                           or str(model_id or "") != str(session.get("model_id") or ""))
        if body.ocr_mode is not None or body.asr_mode is not None or session_changed:
            c.execute(text("""UPDATE chat_sessions
                              SET ocr_mode = coalesce(:ocr, ocr_mode),
                                  asr_mode = coalesce(:asr, asr_mode),
                                  agent_id = :a, model_id = :m,
                                  updated_at = now()
                              WHERE id = :s"""),
                      {"ocr": body.ocr_mode, "asr": body.asr_mode,
                       "a": str(agent_id) if agent_id else None,
                       "m": str(model_id) if model_id else None, "s": str(session_id)})
            session["agent_id"] = agent_id
            session["model_id"] = model_id
    return session, history, user_msg, ocr_mode, asr_mode, agent_id, model_id


def _default_chat_agent(p: Principal, preferred: str = "Asistente del expediente") -> str | None:
    """Agente de chat del sistema elegido automáticamente según la pregunta.

    Las preguntas de contradicción/análisis usan el «Cronista probatorio»
    (Cazador de contradicciones + Cronologista); las de personas/documentos usan
    el «Asistente del expediente» (Relacionador de personas + Analista de
    documento). El usuario no tiene que mencionar el agente ni la skill.
    """
    with tx(p.org_id, p.user_id) as c:
        row = one(c, """SELECT id FROM agents
                         WHERE kind = 'chat' AND is_system = true AND name = :n
                         ORDER BY name LIMIT 1""", n=preferred)
        if not row:
            row = one(c, """SELECT id FROM agents
                             WHERE kind = 'chat' AND is_system = true
                             ORDER BY (name = 'Asistente del expediente') DESC, name
                             LIMIT 1""")
    return str(row["id"]) if row else None


def _entity_question(p: Principal, case_id: UUID, question: str) -> bool:
    """¿La pregunta nombra una persona/entidad del expediente?

    Esas preguntas necesitan `find_person`/grafo, no solo búsqueda de chunks:
    el nombre puede vivir en `entities`/`speakers` y no en el texto de la página.
    """
    match = re.search(r"(?:qui[eé]n es|quien es|qui[eé]n fue|sobre)\s+(.+?)\s*\??$",
                      question or "", re.I)
    if not match:
        return False
    name = " ".join(text_terms.norm(match.group(1)).split())
    if len(name) < 5 or len(name.split()) > 7:
        return False
    parts = [p for p in name.split() if len(p) >= 3]
    if not parts:
        return False
    first, last = parts[0], parts[-1]
    with tx(p.org_id, p.user_id) as c:
        found = one(c, """SELECT 1 FROM entities
                           WHERE case_id = :c
                             AND normalized_name LIKE :a
                             AND normalized_name LIKE :b
                           LIMIT 1""", c=str(case_id), a=f"%{first}%", b=f"%{last}%")
    return bool(found)


def _seed_contradiction(p: Principal, case_id: UUID, question: str) -> list[dict]:
    try:
        with tx(p.org_id, p.user_id) as c:
            return contradiction_seed.for_question(c, str(case_id), question)
    except Exception:  # noqa: BLE001 — la semilla es opcional
        log.debug("contradiction seed failed", exc_info=True)
        return []


def _seed_entity(p: Principal, case_id: UUID, question: str) -> list[dict]:
    try:
        with tx(p.org_id, p.user_id) as c:
            return entity_seed.for_question(c, str(case_id), question)
    except Exception:  # noqa: BLE001 — la semilla es opcional
        log.debug("entity seed failed", exc_info=True)
        return []


def _seed_claim(p: Principal, case_id: UUID, question: str) -> list[dict]:
    try:
        with tx(p.org_id, p.user_id) as c:
            return claim_seed.for_question(c, str(case_id), question)
    except Exception:  # noqa: BLE001 — la semilla es opcional
        log.debug("claim seed failed", exc_info=True)
        return []


def _send_core(case_id: UUID, session_id: UUID, body: ChatMessageIn, request: Request,
               p: Principal, locale: str, case: dict, s, *, emit=None, deadline=None,
               background: BackgroundTasks | None = None) -> dict:
    """Un turno completo, compartido por el POST normal y el SSE."""
    deadline = deadline or deadline_mod.Deadline(s.REQUEST_DEADLINE_MS)
    session, history, user_msg, ocr_mode, asr_mode, agent_id, model_id = _prepare_turn(
        case_id, session_id, body, p)

    # Flujo de corrección DETERMINISTA (Fase 6): si el usuario confirma o cancela y
    # hay una corrección pendiente en esta sesión, se aplica/descarta aquí mismo.
    if correction.is_confirmation(body.content) or correction.is_cancellation(body.content):
        pending = _load_pending(p, case_id, session_id)
        if pending:
            if correction.is_cancellation(body.content):
                _resolve_pending(p, pending["id"], "cancelled")
                return _finish_confirmation(s, p, case_id, session_id, user_msg,
                                            answer=t("messages.correction_cancelled", locale), request=request,
                                            extra={"pending_correction": None},
                                            degraded=deadline.degraded)
            applied = _apply_pending(p, case_id, pending)
            return _finish_confirmation(s, p, case_id, session_id, user_msg, answer=applied["answer"],
                                        request=request,
                                        extra={"corrections_done": [{"tool": pending["tool"],
                                                                     "arguments": pending["arguments"]}],
                                              "corrections_pending": [], "pending_correction": None},
                                        degraded=deadline.degraded)

    if len(body.content.strip()) < 3:
        return _finish_confirmation(s, p, case_id, session_id, user_msg,
                                    answer=t("messages.correction_none", locale), request=request,
                                    answer_kind="partial", intent="dato", degraded=deadline.degraded)

    hints = query_hints.merge(correction.hint(body.content, bool(body.attachments)),
                              query_hints.hint(body.content),
                              role_answer.authoritative_hint(p.org_id, p.user_id, str(case_id), body.content))
    if session.get("summary"):
        hints = query_hints.merge(hints and "\n".join(hints) or None,
                                  f"Resumen de la conversación previa:\n{session['summary']}")

    decision = intent_service.classify(
        body.content, has_attachments=bool(body.attachments),
        is_correction=correction.is_correction(body.content),
        history=history, hints=hints, llm=intent_service.intent_llm())
    # Perfil por usuario: solo actúa si está `active` y la regla no fue fuerte.
    decision = intent_profile.refine(decision, body.content,
                                     org_id=p.org_id, user_id=p.user_id)
    intent_value = str(getattr(decision.intent, "value", decision.intent))
    contradiction_items = _seed_contradiction(p, case_id, body.content)
    party_identity = party_answer.is_identity_question(body.content)
    entity_route = (decision.intent is intent_service.Intent.LOOKUP
                    and _entity_question(p, case_id, body.content))
    if (decision.intent is intent_service.Intent.ANALYSIS or entity_route
            or contradiction_items) and deadline.budget_ms < s.ANALYSIS_DEADLINE_MS:
        # El análisis exhaustivo, la contradicción concreta y el camino de
        # entidad/grafo no caben en 30 s.
        deadline = deadline_mod.Deadline(s.ANALYSIS_DEADLINE_MS)
    selected_agent = None
    if agent_id is None:
        selected_agent = ("Cronista probatorio"
                          if contradiction_items or decision.intent is intent_service.Intent.ANALYSIS
                          else "Asistente del expediente")
        agent_id = _default_chat_agent(p, preferred=selected_agent)
    if contradiction_items or decision.intent is intent_service.Intent.ANALYSIS:
        skill_label = "Cazador de contradicciones / Cronologista"
    elif party_identity:
        skill_label = "Ficha de partes del proceso"
    elif entity_route:
        skill_label = "Relacionador de personas"
    else:
        skill_label = "Analista de documento / evidencia"
    agent_label = selected_agent or ("Agente seleccionado" if session.get("agent_id") else "Asistente del expediente")
    _safe_emit(emit, "routing", {
        "intent": intent_value, "agent": agent_label, "skill": skill_label,
        "label": f"Analizando la pregunta · Agente: {agent_label} · Skill: {skill_label}",
    })

    def _query(strategy: str, seed: list[dict] | None = None) -> dict:
        query_body = QueryIn(question=body.content, strategy=strategy,
                             agent_id=agent_id, model_id=model_id,
                             attachments=body.attachments,
                             ocr_mode=ocr_mode, asr_mode=asr_mode)
        return _run_query(case_id, query_body, request, p, locale, case, s,
                          history=history, hints=hints, ocr_mode=ocr_mode, asr_mode=asr_mode,
                          intent_decision=decision, emit=emit, deadline=deadline,
                          seed_items=seed, background=background)

    if body.strategy == "agent":
        result = _query("agent")
    elif body.strategy == "rag":
        result = _query("rag", _seed_claim(p, case_id, body.content))
    elif party_identity:
        # «¿Cuál es el nombre de la parte demandante?» es identidad, no RAG:
        # la ficha de partes es la fuente y evita mezclar demandantes de etapas.
        result = (_aggregation_result(p, case_id, body.content, locale)
                  or _query("rag", _seed_claim(p, case_id, body.content)))
    elif decision.intent is intent_service.Intent.AGGREGATION:
        result = (_aggregation_result(p, case_id, body.content, locale)
                  or _query("rag", _seed_claim(p, case_id, body.content)))
    elif decision.intent is intent_service.Intent.NAVIGATE:
        _safe_emit(emit, "synthesizing", {"strategy": "locate"})
        result = _navigate_result(p, case_id, body.content, decision, locale,
                                  ocr_mode, asr_mode, s)
    elif decision.intent is intent_service.Intent.PROCEDURAL:
        seed = _process_path_seed(p, case_id, body.content, ocr_mode, asr_mode)
        result = _query("rag", seed)
    elif decision.intent is intent_service.Intent.ANALYSIS:
        result = _query("agent", contradiction_items)
    elif decision.intent is intent_service.Intent.CORRECTION:
        # Aplicar/confirmar correcciones ya se atendió arriba; si no había pendiente,
        # el camino histórico es el agente con la pista de corrección.
        result = _query("agent")
    elif correction.is_reprocess_question(body.content):
        # «¿Cómo mejoro el OCR/ASR?»: diagnosticar exige tools (suggest_reprocess).
        result = _query("agent")
    elif contradiction_items:
        # Aunque el clasificador lo etiquete como «dato», si describe una
        # contradicción registrada, el agente debe ver las dos versiones.
        result = _query("agent", contradiction_items)
    elif decision.intent is intent_service.Intent.LOOKUP and entity_route:
        # «¿Quién es X?» / «¿Qué se dice sobre X?»: el grafo y find_person, no solo chunks.
        result = _query("agent", _seed_entity(p, case_id, body.content))
    else:
        result = _query("rag", _seed_claim(p, case_id, body.content))

    # Los "@" siempre producen tarjeta ver/descargar, aunque el agente no llamara get_file.
    if body.attachments:
        with tx(p.org_id, p.user_id) as c:
            attached_cards = _attachment_file_cards(c, case_id, QueryIn(
                question=body.content, strategy="rag", attachments=body.attachments,
                ocr_mode=ocr_mode, asr_mode=asr_mode))
        known = {card.get("document_id") or card.get("media_id") for card in result.get("file_cards", [])}
        result["file_cards"] = result.get("file_cards", []) + [
            card for card in attached_cards if (card.get("document_id") or card.get("media_id")) not in known]

    # `occurrences` es salida de navegación o de una petición explícita de ubicación.
    if not (body.strategy == "auto" and decision.intent is intent_service.Intent.NAVIGATE):
        if occurrences.should_locate(decision, result.get("answer_kind", ""), body.content):
            term = text_terms.specific_term(body.content) or query_hints.locate_term(body.content)
            if term and occurrences.is_informative(term):
                try:
                    locs = _locate_locations(p, case_id, term, ocr_mode, asr_mode)
                    _, occ = occurrences.build(term, locs, locale, max_items=s.NAV_MAX_OCCURRENCES)
                    result["occurrences"] = occ
                    result["occurrences_term"] = term
                    result["occurrences_note"] = t("messages.occurrences_note", locale).format(term=term)
                except Exception:  # noqa: BLE001 — las ubicaciones nunca rompen el turno
                    log.debug("occurrences failed", exc_info=True)
        elif result.get("answer_kind") in ("insufficient", "correction"):
            result["occurrences"] = []
            result["occurrences_term"] = None
            result["occurrences_note"] = None

    # El agente propuso correcciones: se guardan para la confirmación del usuario.
    pendings = result.get("corrections_pending") or []
    if pendings:
        _store_pending(p, case_id, session_id, pendings)
        result["pending_correction"] = pendings[0]
    elif result.get("corrections_done"):
        _clear_pending(p, case_id, session_id)

    answer_kind = result.get("answer_kind") or "direct"
    intent_out = result.get("intent") or intent_value
    degraded = bool(result.get("degraded") or deadline.degraded)
    result["answer_kind"], result["intent"], result["degraded"] = answer_kind, intent_out, degraded
    result.setdefault("occurrences", [])
    result.setdefault("occurrences_term", None)
    result.setdefault("occurrences_note", None)

    with tx(p.org_id, p.user_id) as c:
        assistant_msg = one(c, """INSERT INTO chat_messages (organization_id, session_id, role, content,
                                     attachments, citations, occurrences, uncertainties, occurrences_term,
                                     occurrences_note, answer_kind, intent, degraded, model_run_id,
                                     intent_confidence, intent_reason, intent_source, router_version)
            VALUES (:o, :s, 'assistant', :t, CAST(:fc AS jsonb), CAST(:ci AS jsonb), CAST(:oc AS jsonb),
                    CAST(:unc AS jsonb), :ot, :on, :ak, :intent, :dg, :mr, :ic, :ir, :isrc, :rv)
            RETURNING id, created_at, answer_kind, intent, degraded""",
                            o=p.org_id, s=str(session_id), t=result["answer"],
                            fc=json.dumps(result.get("file_cards", [])),
                            ci=json.dumps(result.get("citations", [])),
                            oc=json.dumps(result.get("occurrences") or []),
                            unc=json.dumps(result.get("uncertainties") or []),
                            ot=result.get("occurrences_term"), on=result.get("occurrences_note"),
                            ak=answer_kind, intent=intent_out, dg=degraded,
                            mr=result.get("model_run_id"),
                            ic=float(decision.confidence), ir=decision.reason,
                            isrc=decision.source, rv=intent_service.ROUTER_VERSION)
        # Título automático de la primera pregunta y actividad de la sesión.
        title = session["title"] or body.content[:80]
        c.execute(text("UPDATE chat_sessions SET title = :t, updated_at = now() WHERE id = :s"),
                  {"t": title, "s": str(session_id)})
    _update_summary(s, p, session_id, session)
    return {"user_message": user_msg, "assistant_message": assistant_msg, **result}


@router.post("/{session_id}/messages", status_code=201)
def send_message(case_id: UUID, session_id: UUID, body: ChatMessageIn, request: Request,
                 background: BackgroundTasks, p: Principal = Depends(current_principal)):
    s = get_settings()
    case = case_access(p, case_id, "ai.query")
    ratelimit.check("query", p.user_id)
    ratelimit.check_org("query", p.org_id)
    if case["spent_llm_tokens"] >= case["max_llm_tokens"]:
        raise AppError("BUDGET_EXCEEDED", 409)
    locale = negotiate(request.headers.get("accept-language"), p.locale)
    return _send_core(case_id, session_id, body, request, p, locale, case, s,
                      deadline=deadline_mod.Deadline(s.REQUEST_DEADLINE_MS), background=background)


@router.post("/{session_id}/messages/stream")
def stream_message(case_id: UUID, session_id: UUID, body: ChatMessageIn, request: Request,
                   background: BackgroundTasks, p: Principal = Depends(current_principal)):
    """SSE: etapas, tokens del texto ya validado y JSON final. El POST sigue igual."""
    s = get_settings()
    case = case_access(p, case_id, "ai.query")
    ratelimit.check("query", p.user_id)
    ratelimit.check_org("query", p.org_id)
    if case["spent_llm_tokens"] >= case["max_llm_tokens"]:
        raise AppError("BUDGET_EXCEEDED", 409)
    locale = negotiate(request.headers.get("accept-language"), p.locale)

    def generator():
        events: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()

        def emit(event: str, data: dict | None = None) -> None:
            events.put((event, data or {}))

        def worker() -> None:
            try:
                result = _send_core(case_id, session_id, body, request, p, locale, case, s,
                                    emit=emit,
                                    deadline=deadline_mod.Deadline(s.REQUEST_DEADLINE_MS),
                                    background=background)
                events.put(("finished", result))
            except AppError as exc:
                events.put(("error", {"code": exc.code, "status": exc.status,
                                      "message": t(f"errors.{exc.code}", locale)}))
            except Exception:  # noqa: BLE001 — el cliente recibe error, no un cuelgue
                log.exception("stream chat failed")
                events.put(("error", {"code": "INTERNAL_ERROR", "status": 500,
                                      "message": t("errors.INTERNAL_ERROR", locale)}))
            finally:
                events.put(None)

        executor = ThreadPoolExecutor(max_workers=1)
        executor.submit(worker)
        try:
            yield _sse("stage", {"stage": "accepted",
                                 "label": "Preparando la consulta…"})
            while True:
                item = events.get()
                if item is None:
                    break
                event, data = item
                if event == "finished":
                    answer = str(data.get("answer") or "")
                    for chunk in _chunks(answer):
                        yield _sse("token", {"text": chunk})
                    yield _sse("done", data)
                    break
                if event == "done":
                    # `_run_query` marca el fin de etapa; el `done` real lleva el JSON completo.
                    yield _sse("stage", {"stage": "done"})
                    continue
                if event == "token":
                    continue
                if event == "error":
                    yield _sse("error", data)
                    continue
                if event == "tool":
                    yield _sse("stage", {
                        "stage": "retrieving",
                        "label": f"Consultando {data.get('name', 'el expediente')}…",
                        "tool": data.get("name"),
                    })
                    continue
                # routing/retrieving/synthesizing/validating → SIEMPRE evento `stage`.
                yield _sse("stage", {"stage": event, **data})
        finally:
            executor.shutdown(wait=False)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


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
