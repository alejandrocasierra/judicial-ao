"""POST /v1/cases/{id}/query — RAG o agente con herramientas y verificación.

El formato final NO lo decide el modelo: `parse_and_validate` acepta claims con
handles y `compose.render` decide tipo de respuesta, presupuesto de citas y
Markdown. El router solo orquesta (routing → retrieval → síntesis → validación).
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.core.i18n import negotiate, t
from app.providers.llm import get_llm
from app.schemas import QueryIn
from app.security.deps import Principal, case_access, current_principal
from app.services import (agent, answering, audit, cache, case_tools, compose,
                          deadline as deadline_mod, intent as intent_service, intent_profile,
                          metrics, query_hints, ratelimit, role_answer, skills, text_terms,
                          verification)
from app.services.compose import ComposeResult

router = APIRouter(prefix="/cases/{case_id}", tags=["ai"])
log = logging.getLogger(__name__)


def _safe_emit(emit, event: str, data: dict | None = None) -> None:
    """Un emisor defectuoso (SSE cerrado, cliente caído) nunca rompe el turno."""
    if emit is None:
        return
    try:
        emit(event, data or {})
    except Exception:  # noqa: BLE001
        log.debug("emit %s failed", event, exc_info=True)


def _intent_value(decision) -> str:
    return str(getattr(getattr(decision, "intent", None), "value", getattr(decision, "intent", "")) or "")


def _insufficient(case_id: UUID, body: QueryIn, request: Request, p: Principal, locale: str,
                  *, decision=None, degraded: bool = False, emit=None) -> dict:
    with tx(p.org_id, p.user_id) as c:
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="ai.query", entity_type="case",
                     entity_id=str(case_id), after={"mode": body.mode, "strategy": body.strategy, "evidence": 0}, request=request)
    message = t("messages.insufficient_evidence", locale)
    _safe_emit(emit, "done", {"answer_kind": "insufficient", "degraded": degraded,
                              "intent": _intent_value(decision) or "dato", "citations": 0})
    return {"answer": message, "answer_kind": "insufficient", "intent": _intent_value(decision) or "dato",
            "degraded": degraded, "claims": [], "citations": [], "citations_used": [],
            "unsupported_claims": [], "uncertainties": [],
            "related_contradictions": [], "knowledge_type": "case", "file_cards": [], "evidence_count": 0,
            "occurrences": [], "occurrences_term": None, "occurrences_note": None}


def _attachment_dicts(body: QueryIn) -> list[dict]:
    return [{"kind": a.kind, "id": str(a.id), "name": a.name} for a in body.attachments]


def _attachment_file_cards(conn, case_id: UUID, body: QueryIn) -> list[dict]:
    """Tarjetas ver/descargar de los archivos adjuntados con "@" (estrategia RAG)."""
    cards: list[dict] = []
    for a in body.attachments:
        if a.kind == "document":
            r = one(conn, "SELECT id, filename, mime_type, size_bytes, page_count FROM documents WHERE id = :i AND case_id = :c",
                    i=str(a.id), c=str(case_id))
            if r:
                cards.append({"kind": "document", "document_id": str(r["id"]), "media_id": None,
                              "name": a.name or r["filename"], "mime_type": r["mime_type"], "size_bytes": r["size_bytes"],
                              "page_count": r["page_count"], "duration_ms": None,
                              "download_path": f"/cases/{case_id}/documents/{r['id']}/download",
                              "view_path": f"/cases/{case_id}/documents/{r['id']}/download"})
        else:
            r = one(conn, "SELECT id, filename, title, mime_type, size_bytes, duration_ms FROM media WHERE id = :i AND case_id = :c",
                    i=str(a.id), c=str(case_id))
            if r:
                cards.append({"kind": "media", "document_id": None, "media_id": str(r["id"]),
                              "name": a.name or r["title"] or r["filename"], "mime_type": r["mime_type"],
                              "size_bytes": r["size_bytes"], "page_count": None, "duration_ms": r["duration_ms"],
                              "download_path": f"/cases/{case_id}/media/{r['id']}/download",
                              "view_path": f"/cases/{case_id}/media/{r['id']}/download"})
    return cards


def _answer_system_prompt(locale: str, deep: bool = False) -> tuple[str, str, str]:
    """Prompt de síntesis: v3 (análisis exhaustivo) o v2 (turno normal)."""
    return answering.system_prompt(locale, deep=deep)


def _build_plan(question: str, decision, s, history: list[dict] | None):
    try:
        from app.services import retrieval_plan
        return retrieval_plan.build(question, decision, settings=s, history=history)
    except Exception as exc:  # noqa: BLE001 — plan opcional: se usa el retrieval histórico
        log.debug("retrieval_plan unavailable: %s", exc)
        return None


def _retrieve(conn, case_id: str, question: str, *, plan, decision,
              document_ids: list[str] | None = None, media_ids: list[str] | None = None,
              ocr_mode: str | None = None, asr_mode: str | None = None,
              diagnostics: dict | None = None) -> list[dict]:
    kwargs = {"document_ids": document_ids, "media_ids": media_ids,
              "ocr_mode": ocr_mode, "asr_mode": asr_mode}
    try:
        return answering.retrieve(conn, case_id, question, plan=plan,
                                  intent=getattr(decision, "intent", None),
                                  diagnostics=diagnostics, **kwargs)
    except TypeError:  # retrieval histórico (sin plan/intent/diagnostics)
        return answering.retrieve(conn, case_id, question, **kwargs)


def _normalize_seed_items(items: list[dict] | None) -> list[dict]:
    """Los ítems de rol/proceso se citan por su página o segmento de origen."""
    out: list[dict] = []
    for item in items or []:
        candidate = dict(item)
        source = str(candidate.get("source_type") or "")
        if source not in ("document_page", "transcript_segment"):
            if candidate.get("document_id") and candidate.get("page_number") is not None:
                candidate["source_type"] = "document_page"
            elif (candidate.get("media_id") and candidate.get("segment_id")
                  and candidate.get("start_ms") is not None):
                candidate["source_type"] = "transcript_segment"
        out.append(candidate)
    return out


def _pg_array(values: list[object]) -> str:
    return "{" + ",".join(str(v) for v in values) + "}"


ANSWER_CACHE_VERSION = "r1-2026.10.22"
_CONTEXTUAL_QUESTION = re.compile(
    r"\b(lo anterior|eso|esto|el mismo|la misma|dich[oa]|anterior|sigue|entonces|"
    r"y el|y la|tambi[eé]n|aquel|aquella)\b", re.I)


def _answer_cache_key(conn, case_id: str, body, p: Principal, s,
                      history: list[dict] | None, locale: str,
                      ocr_mode: str | None, asr_mode: str | None) -> str | None:
    """Clave conservadora para reutilizar una respuesta ya calculada.

    Incluye usuario, expediente, modelo, agente, estrategia, versión de pipeline,
    evidencia y (solo si la pregunta es dependiente del contexto) los últimos
    turnos. Un expediente recién subido/reprocesado cambia la clave.
    """
    if body.attachments or not getattr(s, "ANSWER_CACHE_TTL_SECONDS", 0):
        return None
    try:
        evidence = one(conn, """
            SELECT
              (SELECT count(*) FROM documents WHERE case_id = :c) AS docs,
              coalesce((SELECT max(created_at)::text FROM documents WHERE case_id = :c), '') AS docs_at,
              (SELECT count(*) FROM media WHERE case_id = :c) AS media,
              coalesce((SELECT max(created_at)::text FROM media WHERE case_id = :c), '') AS media_at,
              (SELECT count(*) FROM document_pages dp
                 JOIN documents d ON d.id = dp.document_id WHERE d.case_id = :c) AS pages,
              (SELECT count(*) FROM transcript_segments ts
                 JOIN media m ON m.id = ts.media_id WHERE m.case_id = :c) AS segments,
              (SELECT count(*) FROM claims WHERE case_id = :c) AS claims,
              (SELECT count(*) FROM contradictions WHERE case_id = :c) AS contradictions
        """, c=str(case_id))
    except Exception:  # noqa: BLE001 — sin versión de evidencia no se cachea
        return None
    history_hash = ""
    if _CONTEXTUAL_QUESTION.search(body.question or ""):
        history_hash = hashlib.sha256(json.dumps(
            [{"role": str(turn.get("role") or ""),
              "content": str(turn.get("content") or "")[:500]}
             for turn in (history or [])[-4:]],
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    raw = "|".join([
        ANSWER_CACHE_VERSION,
        str(s.PIPELINE_VERSION),
        str(p.user_id),
        str(case_id),
        str(body.model_id or ""),
        str(body.agent_id or ""),
        str(body.strategy or ""),
        text_terms.norm(body.question),
        locale,
        ocr_mode or "",
        asr_mode or "",
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str),
        history_hash,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _clean_ocr_context(llm, snippet: str) -> str:
    """Reescribe el fragmento OCR sucio en una frase jurídica, sin añadir hechos."""
    if not snippet:
        return ""
    system = (
        "Eres editor jurídico. Reescribe el fragmento OCR en UNA sola oración "
        "clara y fiel. No añadas hechos, cifras, nombres ni atribuciones que no "
        "estén en el fragmento. Si empieza o termina a mitad de palabra, ignora "
        "esa parte; no uses corchetes, puntos suspensivos ni marcas de corte. "
        "No expliques el proceso. Devuelve solo la oración."
    )
    try:
        result = llm.complete(system, f"<fragmento>{html.escape(snippet, quote=False)}</fragmento>")
        text = str(getattr(result, "text", result) or "").strip()
        text = re.sub(r"^```.*?```$", "", text, flags=re.S).strip()
        return " ".join(text.split())[:600]
    except Exception:  # noqa: BLE001 — si falla, se usa el fragmento original
        return ""


def _seed_handle(seed: dict, items: list[dict]) -> str | None:
    """Handle visible que corresponde a la página/segmento de una semilla."""
    for item in items:
        same_page = (seed.get("document_id") and item.get("document_id")
                     and str(seed["document_id"]) == str(item["document_id"])
                     and seed.get("page_number") == item.get("page_number"))
        same_segment = (seed.get("media_id") and item.get("media_id")
                        and str(seed["media_id"]) == str(item["media_id"])
                        and str(seed.get("segment_id")) == str(item.get("segment_id")))
        if (same_page or same_segment) and item.get("handle"):
            return str(item["handle"])
    return None


def _resolve_primary_texts(conn, items: list[dict]) -> list[dict]:
    """Sustituye el texto del chunk por el texto real de la página/segmento citado.

    El agente busca en fragmentos y puede citar una página cuyo texto no contiene
    la frase que el fragmento sí traía. La respuesta final debe validarse y
    citarse contra lo que el usuario verá en la fuente, no contra el chunk.
    """
    if not items:
        return items
    doc_pairs = {(str(it.get("document_id")), int(it["page_number"]))
                 for it in items
                 if it.get("document_id") and it.get("page_number") is not None}
    segment_ids = {str(it.get("segment_id")) for it in items if it.get("segment_id")}
    pages: dict[tuple[str, int], dict] = {}
    segments: dict[str, dict] = {}
    if doc_pairs:
        found = rows(conn, """
            SELECT document_id::text AS document_id, page_number, text, folio,
                   ocr_confidence, needs_review
              FROM document_pages
             WHERE document_id = ANY(CAST(:docs AS uuid[]))
               AND page_number = ANY(CAST(:pages AS integer[]))
        """, docs=_pg_array(sorted({d for d, _ in doc_pairs})),
             pages=_pg_array(sorted({p for _, p in doc_pairs})))
        for row in found:
            pages[(str(row["document_id"]), int(row["page_number"]))] = row
    if segment_ids:
        found = rows(conn, """
            SELECT id::text AS id, text, start_ms, end_ms, confidence, needs_review
              FROM transcript_segments
             WHERE id = ANY(CAST(:ids AS uuid[]))
        """, ids=_pg_array(sorted(segment_ids)))
        for row in found:
            segments[str(row["id"])] = row
    out: list[dict] = []
    for item in items:
        resolved = dict(item)
        pair = ((str(item.get("document_id")), int(item["page_number"]))
                if item.get("document_id") and item.get("page_number") is not None else None)
        segment_id = str(item.get("segment_id")) if item.get("segment_id") else None
        if pair is not None and pair in pages:
            page = pages[pair]
            if page.get("text"):
                resolved["text"] = page["text"]
            resolved["source_type"] = "document_page"
            if page.get("folio"):
                resolved["folio"] = page["folio"]
            for key in ("ocr_confidence", "needs_review"):
                if page.get(key) is not None:
                    resolved[key] = page[key]
        elif segment_id is not None and segment_id in segments:
            segment = segments[segment_id]
            if segment.get("text"):
                resolved["text"] = segment["text"]
            resolved["source_type"] = "transcript_segment"
            for key in ("start_ms", "end_ms"):
                if segment.get(key) is not None:
                    resolved[key] = segment[key]
            if segment.get("confidence") is not None:
                resolved["asr_confidence"] = segment["confidence"]
            if segment.get("needs_review") is not None:
                resolved["needs_review"] = segment["needs_review"]
            if resolved.get("start_ms") is not None:
                resolved["start_mmss"] = text_terms.mmss(resolved["start_ms"])
        out.append(resolved)
    return out


def _deadline_claims(items: list[dict]) -> list[dict]:
    """Mejor respuesta disponible sin LLM cuando el presupuesto ya se agotó."""
    claims: list[dict] = []
    for item in items:
        handle = item.get("handle")
        if not handle or not compose._is_citable(item):
            continue
        body_text = " ".join(str(item.get("text") or "").split())[:320]
        if body_text:
            claims.append({"text": body_text, "citations": [str(handle)]})
        if len(claims) >= 2:
            break
    return claims


@router.post("/query")
def query(case_id: UUID, body: QueryIn, request: Request, background: BackgroundTasks,
          p: Principal = Depends(current_principal)):
    s = get_settings()
    if len(body.question) > s.QUESTION_MAX_CHARS:
        raise AppError("QUESTION_TOO_LONG", 422)
    case = case_access(p, case_id, "ai.query")
    ratelimit.check("query", p.user_id)
    ratelimit.check_org("query", p.org_id)
    if case["spent_llm_tokens"] >= case["max_llm_tokens"]:
        raise AppError("BUDGET_EXCEEDED", 409)
    locale = negotiate(request.headers.get("accept-language"), p.locale)

    with metrics.track_stage("query_total", p.org_id):
        hints = query_hints.merge(query_hints.hint(body.question),
                                  role_answer.authoritative_hint(p.org_id, p.user_id, str(case_id), body.question))
        return _run_query(case_id, body, request, p, locale, case, s, hints=hints,
                          ocr_mode=body.ocr_mode, asr_mode=body.asr_mode, background=background)


def _mentioned_document_ids(conn, case_id: str, question: str) -> list[str]:
    """Documentos nombrados literalmente en la pregunta («¿qué dice X.pdf?»).

    Sin esto, el nombre del archivo no está en el texto de los chunks: la
    búsqueda híbrida devolvía 0 y el chat decía «no hay evidencia» sobre un
    documento que el usuario acababa de nombrar.
    """
    from app.services import text_terms

    def _squash(value: str) -> str:
        return re.sub(r"[^0-9a-z]", "", text_terms.norm(value or ""))

    flat_question = _squash(question)
    if not flat_question:
        return []
    found: list[str] = []
    for doc in rows(conn, "SELECT id, filename FROM documents WHERE case_id = :c",
                    c=str(case_id)):
        name = str(doc.get("filename") or "")
        flat_name = _squash(name)
        flat_stem = _squash(name.rsplit(".", 1)[0])
        if (len(flat_name) >= 12 and flat_name in flat_question) or (
                len(flat_stem) >= 12 and flat_stem in flat_question):
            found.append(str(doc["id"]))
            if len(found) >= 3:
                break
    return found


def _run_query(case_id: UUID, body: QueryIn, request: Request, p: Principal, locale: str, case: dict, s,
               history: list[dict] | None = None, hints: list[str] | None = None,
               ocr_mode: str | None = None, asr_mode: str | None = None,
               intent_decision=None, emit=None, deadline=None, seed_items: list[dict] | None = None,
               background=None, verify_mode: str | None = None) -> dict:
    from app.providers.llm import get_llm_for_model

    try:
        llm = get_llm_for_model(str(body.model_id), p.org_id, p.user_id) if body.model_id else get_llm()
    except LookupError:
        raise AppError("MODEL_NOT_FOUND", 404, {
            "detail": "El modelo seleccionado ya no existe o no pertenece a la organización."}) from None
    file_cards: list[dict] = []
    corrections_pending: list[dict] = []
    corrections_done: list[dict] = []
    coverage: float | None = None
    tool_calls: list[dict] = []

    # Pregunta repetida del mismo usuario/expediente/modelo: reutilizar la
    # respuesta completa ahorra retrieval, agente y tokens. La clave incluye la
    # versión de evidencia y de pipeline, así que un expediente nuevo no la usa.
    answer_key = None
    try:
        with tx(p.org_id, p.user_id) as c:
            answer_key = _answer_cache_key(c, str(case_id), body, p, s, history,
                                           locale, ocr_mode, asr_mode)
        if answer_key:
            cached = cache.get_json("answer", answer_key)
            if isinstance(cached, dict) and cached.get("answer"):
                cached = dict(cached)
                cached["cached"] = True
                _safe_emit(emit, "done", {"answer_kind": cached.get("answer_kind"),
                                          "cached": True})
                return cached
    except Exception:  # noqa: BLE001 — la caché nunca rompe el turno
        log.debug("answer cache read failed", exc_info=True)

    deadline = deadline or deadline_mod.Deadline(s.REQUEST_DEADLINE_MS)
    decision = intent_decision
    if decision is None:
        decision = intent_service.classify(body.question, has_attachments=bool(body.attachments),
                                           history=history, hints=hints,
                                           llm=intent_service.intent_llm())
        # Mismo perfil que en el chat: solo decide si está `active` y la regla no fue fuerte.
        decision = intent_profile.refine(decision, body.question,
                                         org_id=p.org_id, user_id=p.user_id)
    deep = (decision.intent is intent_service.Intent.ANALYSIS
            or any(str(item.get("seed") or "") == "contradiction"
                   for item in (seed_items or [])))
    if deep and deadline.budget_ms < s.ANALYSIS_DEADLINE_MS:
        # Un análisis exhaustivo no cabe en 12-15 s: se le da su propio presupuesto.
        deadline = deadline_mod.Deadline(s.ANALYSIS_DEADLINE_MS)
    intent_value = _intent_value(decision) or "dato"
    # En el chat, `_send_core` ya emitió routing con agente/skill. Solo el
    # endpoint directo /query necesita este evento para no duplicar la etapa.
    if intent_decision is None:
        _safe_emit(emit, "routing", {
            "intent": intent_value,
            "label": "Analizando la pregunta…",
        })

    if body.strategy == "agent":
        agent_prompt = None
        allowed_tools = None
        agent_parameters: dict = {}
        if body.agent_id:
            with tx(p.org_id, p.user_id) as c:
                config = skills.load_config(c, str(body.agent_id))
                agent_prompt = config["prompt"]
                allowed_tools = config["allowed_tools"]
                agent_parameters = config.get("parameters") or {}
        # Ítems deterministas de rol/proceso: evitan que el agente abstenga.
        seed = _normalize_seed_items(seed_items) if seed_items is not None else \
            role_answer.authoritative_evidence(str(p.org_id), str(p.user_id), str(case_id), body.question)
        with deadline.stage("agent"):
            _safe_emit(emit, "retrieving", {
                "strategy": "agent",
                "label": "Investigando con el agente y sus herramientas…",
            })
            max_steps = s.DEEP_MAX_STEPS if deep else 5
            with tx(p.org_id, p.user_id) as c:
                agent_result = agent.run_agent_query(
                    c, str(case_id), body.question, locale,
                    agent_prompt=agent_prompt, llm=llm,
                    attachments=_attachment_dicts(body),
                    org_id=str(p.org_id), actor_id=str(p.user_id),
                    history_turns=history, hints=hints, seed_evidence=seed,
                    ocr_mode=ocr_mode, asr_mode=asr_mode,
                    emit=emit, allowed_tools=allowed_tools, verify=False, max_steps=max_steps,
                    parameters=agent_parameters, deep=deep)
        # Propagación diferida por las tools de escritura (reindex pgvector, rebuild del
        # grafo, encolado de jobs): solo DESPUÉS de confirmar la transacción del loop.
        case_tools.drain_actions(agent_result.pop("post_commit", []))
        items = agent_result.get("evidence", [])
        file_cards = agent_result.get("file_cards", [])
        corrections_pending = agent_result.get("corrections_pending", [])
        corrections_done = agent_result.get("corrections_done", [])
        tool_calls = agent_result.get("tool_calls", [])
        if agent_result.get("degraded"):
            deadline.degraded = True
        if not items:
            if not file_cards and not corrections_pending:
                return _insufficient(case_id, body, request, p, locale, decision=decision,
                                     degraded=deadline.degraded, emit=emit)
            # Respuesta sin claims citables: tarjetas de archivo y/o propuesta de corrección.
            answer = ""
            if corrections_pending:
                answer = ("\n\n".join(c.get("summary", "") for c in corrections_pending)
                          + "\n\n" + t("messages.correction_confirm_prompt", locale))
            with tx(p.org_id, p.user_id) as c:
                audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="ai.query", entity_type="case",
                             entity_id=str(case_id), after={"mode": body.mode, "strategy": body.strategy,
                                                            "evidence": 0, "file_cards": len(file_cards),
                                                            "corrections_pending": len(corrections_pending)}, request=request)
            _safe_emit(emit, "done", {"answer_kind": "partial", "degraded": deadline.degraded,
                                      "intent": intent_value, "citations": 0})
            return {"answer": answer, "answer_kind": "partial", "intent": intent_value,
                    "degraded": deadline.degraded, "claims": [], "citations": [], "citations_used": [],
                    "unsupported_claims": [], "uncertainties": [], "related_contradictions": [],
                    "knowledge_type": "case", "file_cards": file_cards,
                    "corrections_pending": corrections_pending, "corrections_done": corrections_done,
                    "pending_correction": corrections_pending[0] if corrections_pending else None,
                    "evidence_count": 0, "occurrences": [], "occurrences_term": None,
                    "occurrences_note": None}
        validated = {
            "claims": agent_result["claims"],
            "unsupported_claims": agent_result["unsupported_claims"],
            "uncertainties": agent_result["uncertainties"],
            "schema_valid": agent_result.get("schema_valid", True),
        }
        result = SimpleNamespace(
            provider=agent_result["provider"], model=agent_result["model"],
            tokens_in=agent_result["tokens_in"], tokens_out=agent_result["tokens_out"],
        )
        prompt_id, prompt_version = agent_result["prompt_id"], agent_result["prompt_version"]
        task = "agent_query"
        raw_text = agent_result.get("raw_text", "")
    else:
        plan = _build_plan(body.question, decision, s, history)
        diagnostics: dict = {}
        with deadline.stage("retrieving"):
            _safe_emit(emit, "retrieving", {
                "strategy": "rag",
                "label": "Investigando el expediente (OCR/ASR/pgvector)…",
            })
            doc_ids = [str(a.id) for a in body.attachments if a.kind == "document"]
            media_ids = [str(a.id) for a in body.attachments if a.kind == "media"]
            if not doc_ids:
                with tx(p.org_id, p.user_id) as c_docs:
                    doc_ids = _mentioned_document_ids(c_docs, str(case_id), body.question)
            scoped_future = None
            executor = None
            if doc_ids or media_ids:
                # Los "@" se recuperan en paralelo con la búsqueda global, cada
                # uno con su propia conexión/transacción (RLS incluido).
                def _scoped() -> list[dict]:
                    with tx(p.org_id, p.user_id) as sc:
                        return _retrieve(sc, str(case_id), body.question, plan=plan, decision=decision,
                                         document_ids=doc_ids or None, media_ids=media_ids or None,
                                         ocr_mode=ocr_mode, asr_mode=asr_mode)
                executor = ThreadPoolExecutor(max_workers=1)
                scoped_future = executor.submit(_scoped)
            try:
                with tx(p.org_id, p.user_id) as c:
                    items = _retrieve(c, str(case_id), body.question, plan=plan, decision=decision,
                                      ocr_mode=ocr_mode, asr_mode=asr_mode, diagnostics=diagnostics)
                    if body.attachments:
                        file_cards = _attachment_file_cards(c, case_id, body)
                if scoped_future is not None:
                    try:
                        scoped = scoped_future.result()
                    except Exception:  # noqa: BLE001 — el alcance "@" no rompe la global
                        log.debug("scoped retrieval failed", exc_info=True)
                        scoped = []
                    if scoped:
                        items = answering.merge_items(scoped, items)
            finally:
                if executor is not None:
                    executor.shutdown(wait=False)
            coverage = diagnostics.get("coverage")
        if seed_items:
            # Cadena procesal / lista de rol: entra como evidencia de primera clase.
            try:
                items = answering.merge_items(_normalize_seed_items(seed_items), items)
            except Exception:  # noqa: BLE001
                items = _normalize_seed_items(seed_items) + list(items or [])
        if not items:
            return _insufficient(case_id, body, request, p, locale, decision=decision,
                                 degraded=deadline.degraded, emit=emit)
        system, prompt_id, prompt_version = _answer_system_prompt(locale, deep=deep)
        task = "answer_question"
        _safe_emit(emit, "reviewing", {
            "label": "Revisando la evidencia recuperada…",
            "evidence": len(items),
        })
        _safe_emit(emit, "synthesizing", {
            "label": "Redactando la respuesta…",
            "evidence": len(items),
        })
        synthesis_failed = False
        if deadline.can("synthesizing", 50):
            with deadline.stage("synthesizing"):
                try:
                    result = llm.complete(system, answering.build_user_prompt(
                        body.question, items, history=history, deep=deep))
                    raw_text = result.text
                except Exception as exc:  # noqa: BLE001 — proveedor caído: compositor con evidencia
                    log.warning("synthesis failed: %s", exc)
                    deadline.degraded = True
                    synthesis_failed = True
                    result = SimpleNamespace(provider=getattr(llm, "name", ""), model=getattr(llm, "model", ""),
                                             tokens_in=0, tokens_out=0)
                    raw_text = ""
        else:
            deadline.degraded = True
            synthesis_failed = True
            result = SimpleNamespace(provider=getattr(llm, "name", ""), model=getattr(llm, "model", ""),
                                     tokens_in=0, tokens_out=0)
            raw_text = ""
        if raw_text:
            validated = answering.parse_and_validate(raw_text, items, s.ANSWER_MIN_GROUNDING_OVERLAP)
        elif synthesis_failed:
            # Solo se usa la mejor evidencia disponible si el LLM no respondió por
            # fallo/deadline. Un JSON malformado o vacío NO autoriza a inventar claims.
            validated = {"claims": _deadline_claims(items), "unsupported_claims": [],
                         "uncertainties": [], "schema_valid": False}
        else:
            validated = {"claims": [], "unsupported_claims": [], "uncertainties": [],
                         "schema_valid": False}

    # La cita final se resuelve a la página/segmento que el usuario verá. Si el
    # chunk del agente no coincide con esa fuente, la afirmación se descarta o se
    # reasigna a la fuente que sí la sostiene.
    with tx(p.org_id, p.user_id) as c:
        items = _resolve_primary_texts(c, items)
    if validated.get("claims"):
        revalidated = answering.revalidate_claims(
            validated["claims"], items, s.ANSWER_MIN_GROUNDING_OVERLAP)
        validated["claims"] = revalidated["claims"]
        known = {str(u.get("text")) for u in (validated.get("unsupported_claims") or [])}
        validated["unsupported_claims"] = list(validated.get("unsupported_claims") or []) + [
            u for u in revalidated["unsupported_claims"] if str(u.get("text")) not in known]
    mode = verify_mode or s.VERIFY_MODE
    if verify_mode is None and mode == "sync" and body.strategy != "agent":
        # En RAG general la afirmación ya se validó contra la página/segmento
        # primario y sus números. El juez síncrono se reserva para el agente y
        # para preguntas de un claim concreto (donde el modelo tiende a añadir
        # comparaciones o cargos que la cita no contiene).
        if not any(item.get("claim_text") for item in (seed_items or [])):
            mode = "async"
    semantic_verdicts: list[dict] = []
    if mode == "sync" and validated["claims"] and not deadline.expired:
        pre_sync_claims = list(validated["claims"])
        try:
            judge_llm = None
            if s.VERIFY_MODEL_ID:
                try:
                    judge_llm = get_llm_for_model(s.VERIFY_MODEL_ID, p.org_id, p.user_id)
                except Exception:  # noqa: BLE001 — cae al juez por routing/default
                    judge_llm = None
            semantic_verdicts = verification.verify(validated["claims"], items, llm=judge_llm)
            verification.apply_verdicts(validated, semantic_verdicts)
        except Exception as exc:  # noqa: BLE001 — el juez no bloquea la respuesta
            log.warning("sync verification failed: %s", exc)
        if not validated.get("claims") and body.strategy == "agent":
            # Un juez demasiado estricto no puede convertir una respuesta con
            # evidencia en una abstención. Se conservan las afirmaciones que ya
            # pasaron grounding/números/nombres y se marcan para revisión.
            validated["claims"] = pre_sync_claims
            restored = {text_terms.norm(str(c.get("text") or "")) for c in pre_sync_claims}
            validated["unsupported_claims"] = [
                u for u in (validated.get("unsupported_claims") or [])
                if text_terms.norm(str(u.get("text") or "")) not in restored]
            note = ("El juez semántico no pudo verificar estas afirmaciones; "
                    "requieren revisión humana.")
            if note not in validated["uncertainties"]:
                validated["uncertainties"].append(note)

    # Una contradicción concreta SIEMPRE muestra sus dos versiones registradas,
    # aunque el LLM haya sintetizado solo una. Las dos claim de la base son la
    # definición del objeto que el usuario pregunta.
    if seed_items:
        seeded_claims: list[dict] = []
        seeded_texts: set[str] = set()
        for seed in seed_items:
            if str(seed.get("seed") or "") != "contradiction":
                continue
            claim_text = str(seed.get("claim_text") or "").strip()
            handle = _seed_handle(seed, items)
            key = text_terms.norm(claim_text)
            if claim_text and handle and key not in seeded_texts:
                seeded_claims.append({"text": claim_text, "citations": [handle]})
                seeded_texts.add(key)
        if len(seeded_claims) >= 2:
            # Explicación mínima y determinista: contrapone las dos afirmaciones
            # registradas, sin inventar cifras ni atribuciones.
            first = str(seeded_claims[0]["text"]).rstrip(".")
            second = str(seeded_claims[1]["text"]).rstrip(".")
            for article in ("El ", "La ", "Los ", "Las ", "Un ", "Una "):
                if first.startswith(article):
                    first = article.lower() + first[len(article):]
                    break
            for article in ("El ", "La ", "Los ", "Las ", "Un ", "Una "):
                if second.startswith(article):
                    second = article.lower() + second[len(article):]
                    break
            summary = f"La contradicción consiste en que {first}, mientras que {second}."
            seeded_claims.insert(0, {"text": summary,
                                     "citations": [seeded_claims[0]["citations"][0]]})
        context_seed = next((seed for seed in seed_items
                             if str(seed.get("seed") or "") == "contradiction"
                             and seed.get("context_text")), None)
        if context_seed is not None:
            context_handle = _seed_handle(context_seed, items)
            if context_handle:
                raw_context = str(context_seed["context_text"])[:600]
                cleaned_context = _clean_ocr_context(llm, raw_context)
                seeded_claims.append({
                    "text": cleaned_context or raw_context,
                    "citations": [context_handle],
                })
        if seeded_claims:
            validated["claims"] = seeded_claims + [
                claim for claim in (validated.get("claims") or [])
                if text_terms.norm(str(claim.get("text") or "")) not in seeded_texts]

    # Último recurso: si la síntesis no produjo ninguna afirmación pero la
    # pregunta tiene una semilla de la base (contradicción o claim), se muestran
    # las afirmaciones registradas con su fuente. Mejor evidencia que abstención.
    if not validated.get("claims") and seed_items:
        by_text: dict[str, str] = {}
        for seed in seed_items:
            claim_text = str(seed.get("claim_text") or "").strip()
            if not claim_text:
                continue
            for item in items:
                same_page = (seed.get("document_id") and item.get("document_id")
                             and str(seed["document_id"]) == str(item["document_id"])
                             and seed.get("page_number") == item.get("page_number"))
                same_segment = (seed.get("media_id") and item.get("media_id")
                                and str(seed["media_id"]) == str(item["media_id"])
                                and str(seed.get("segment_id")) == str(item.get("segment_id")))
                if (same_page or same_segment) and item.get("handle"):
                    by_text.setdefault(claim_text, str(item["handle"]))
                    break
        if by_text:
            validated["claims"] = [{"text": claim_text, "citations": [handle]}
                                   for claim_text, handle in list(by_text.items())[:4]]
            note = ("No se pudo sintetizar una respuesta; se muestran las afirmaciones "
                    "registradas en la base con su fuente.")
            if note not in validated["uncertainties"]:
                validated["uncertainties"].append(note)

    # Los límites solo pueden hablar de las fuentes que el usuario verá citadas.
    visible_handles = {str(handle)
                       for claim in (validated.get("claims") or [])
                       for handle in (claim.get("citations") or [])}
    visible_items = [item for item in items if str(item.get("handle")) in visible_handles]
    validated["uncertainties"] = answering.clean_uncertainties(
        validated.get("uncertainties") or [], visible_items or items, locale)

    _safe_emit(emit, "validating", {
        "claims": len(validated["claims"]),
        "label": "Verificando citas, cifras y nombres…",
    })
    with deadline.stage("compose"):
        composed: ComposeResult = compose.render(
            body.question, intent=getattr(decision, "intent", None), claims=validated["claims"],
            evidence=items, uncertainties=validated["uncertainties"], locale=locale,
            max_citations=(s.DEEP_MAX_VISIBLE_CITATIONS if deep else s.MAX_VISIBLE_CITATIONS),
            max_per_file=(s.DEEP_MAX_CITATIONS_PER_FILE if deep else s.MAX_CITATIONS_PER_FILE),
            coverage=coverage, deep=deep)

    # Preguntas de rol sin claims del modelo: lista determinista como respuesta.
    if composed.answer_kind == compose.INSUFFICIENT:
        role = query_hints.role_from_text(body.question)
        deterministic = role_answer.render(role, items) if role else ""
        if deterministic:
            used = [str(it["handle"]) for it in items if it.get("handle") and compose._is_citable(it)][:10]
            composed = ComposeResult(answer_kind=compose.DIRECT, answer=deterministic,
                                     used_handles=used,
                                     sections={"direct": [deterministic], "evidence": [], "limits": [],
                                               "sources": []},
                                     uncertainties=composed.uncertainties)

    by_handle = {str(it["handle"]): it for it in items if it.get("handle")}
    degraded = bool(deadline.degraded or deadline.expired)
    output_payload = {"raw_text": (raw_text or "")[:20000], "claims": validated["claims"],
                      "unsupported_claims": validated["unsupported_claims"],
                      "uncertainties": composed.uncertainties, "answer_kind": composed.answer_kind,
                      "intent": intent_value, "degraded": degraded, "tool_calls": tool_calls}
    validation_payload = {"schema_valid": validated.get("schema_valid", True),
                          "unsupported": len(validated["unsupported_claims"]),
                          "answer_kind": composed.answer_kind}
    if semantic_verdicts:
        validation_payload["semantic_verdicts"] = semantic_verdicts

    with tx(p.org_id, p.user_id) as c:
        run = one(c, """INSERT INTO model_runs (organization_id, case_id, task, provider, model, prompt_id, prompt_version,
              pipeline_version, input_hash, output, validation, tokens_in, tokens_out, actor_id)
            VALUES (:o,:c,:task,:pr,:m,:pid,:pv,:plv,:ih,CAST(:out AS jsonb),CAST(:val AS jsonb),:ti,:to,:u) RETURNING id""",
                  o=p.org_id, c=str(case_id), task=task, pr=result.provider, m=result.model, pid=prompt_id, pv=prompt_version,
                  plv=s.PIPELINE_VERSION, ih=answering.input_hash(body.question, items),
                  out=json.dumps(output_payload, ensure_ascii=False, default=str),
                  val=json.dumps(validation_payload, ensure_ascii=False, default=str),
                  ti=result.tokens_in, to=result.tokens_out, u=p.user_id)
        c.execute(text("UPDATE cases SET spent_llm_tokens = spent_llm_tokens + :n WHERE id = :c"),
                  {"n": result.tokens_in + result.tokens_out, "c": str(case_id)})
        handle_to_cit: dict[str, str] = {}
        # Solo las citas que el compositor usó, en SU orden (marker 1..N).
        for handle in composed.used_handles:
            it = by_handle.get(handle)
            if not it or not compose._is_citable(it):
                continue
            if it.get("document_id"):
                cid = one(c, """INSERT INTO citations (organization_id, case_id, target_type, target_id, source_type, document_id, page_number, folio)
                    VALUES (:o,:c,'answer',:r,'document_page',:d,:pg,:f) RETURNING id""",
                          o=p.org_id, c=str(case_id), r=run["id"], d=str(it["document_id"]),
                          pg=it.get("page_number"), f=it.get("folio"))["id"]
            elif it.get("media_id") and it.get("segment_id"):
                cid = one(c, """INSERT INTO citations (organization_id, case_id, target_type, target_id, source_type, media_id, segment_id, start_ms, end_ms)
                    VALUES (:o,:c,'answer',:r,'transcript_segment',:m,:sg,:s,:e) RETURNING id""",
                          o=p.org_id, c=str(case_id), r=run["id"], m=str(it["media_id"]), sg=str(it["segment_id"]),
                          s=it.get("start_ms"), e=it.get("end_ms"))["id"]
            else:
                continue
            handle_to_cit[handle] = str(cid)
        pages = [(str(i["document_id"]), i["page_number"]) for i in items
                 if i.get("source_type") == "document_page" and i.get("document_id")]
        segs = [str(i["segment_id"]) for i in items
                if i.get("source_type") == "transcript_segment" and i.get("segment_id")]
        related = rows(c, """SELECT DISTINCT co.id, co.contradiction_type, co.description, co.severity FROM contradictions co
            JOIN citations ci ON ci.target_type='claim' AND ci.target_id IN (co.claim_a_id, co.claim_b_id)
            WHERE co.case_id = :c AND (ci.segment_id = ANY(CAST(:segs AS uuid[]))
               OR (ci.document_id::text || ':' || ci.page_number) = ANY(CAST(:pages AS text[])))""",
                       c=str(case_id), segs="{" + ",".join(segs) + "}",
                       pages="{" + ",".join(f'"{d}:{n}"' for d, n in pages) + "}")
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="ai.query", entity_type="case", entity_id=str(case_id),
                     after={"mode": body.mode, "strategy": body.strategy, "model_run_id": str(run["id"]),
                            "evidence": len(items), "answer_kind": composed.answer_kind,
                            "unsupported": len(validated["unsupported_claims"])}, request=request)

    # Verificación semántica fuera de la ruta crítica.
    if mode == "async" and validated["claims"]:
        if background is not None:
            try:
                background.add_task(verification.verify_and_store, str(p.org_id), str(p.user_id),
                                    str(run["id"]), validated["claims"], items)
            except Exception:  # noqa: BLE001
                log.debug("could not enqueue verification", exc_info=True)

    # Métricas de observabilidad (SSD §28)
    metrics.track_llm_usage(p.org_id, result.provider, result.model, task, result.tokens_in, result.tokens_out, 0.0)
    if validated["claims"]:
        cited = sum(1 for cl in validated["claims"] if cl["citations"])
        metrics.update_citation_accuracy(p.org_id, str(case_id), cited / len(validated["claims"]))
    metrics.update_retrieval_hit_rate(p.org_id, str(case_id), len(handle_to_cit) / len(items) if items else 0.0)

    claims = []
    for cl in validated["claims"]:
        ids = [handle_to_cit[h] for h in cl["citations"] if h in handle_to_cit]
        if ids:
            claims.append({"text": cl["text"], "citations": ids})
    uncertainties: list[str] = []
    if composed.answer_kind != compose.INSUFFICIENT:
        for value in composed.uncertainties:
            if value and value not in uncertainties:
                uncertainties.append(value)
    else:
        # La respuesta ya dice que no hay evidencia: no repetimos el mismo aviso
        # como advertencia aparte. Solo se conservan límites con información nueva.
        for value in composed.uncertainties:
            low = (value or "").lower()
            if value and value not in uncertainties and "evidencia" not in low and "evidence" not in low:
                uncertainties.append(value)
    if validated["unsupported_claims"]:
        note = t("messages.unsupported_claims_removed", locale)
        if note not in uncertainties:
            uncertainties.append(note)
    if related:
        note = t("messages.contradiction_requires_review", locale)
        if note not in uncertainties:
            uncertainties.append(note)

    citations = []
    for marker, handle in enumerate(composed.used_handles, start=1):
        cid = handle_to_cit.get(handle)
        if not cid:
            continue
        it = by_handle[handle]
        item_meta = it.get("metadata") or {}
        mode_meta = {"ocr_mode": it.get("ocr_mode") or item_meta.get("ocr_mode"),
                     "asr_mode": it.get("asr_mode") or item_meta.get("asr_mode")}
        if it.get("document_id"):
            meta = {"document_id": str(it["document_id"]), "page": it.get("page_number"),
                    "folio": it.get("folio"), "filename": it.get("filename")}
            source_type = "document_page"
        else:
            meta = {"media_id": str(it.get("media_id")) if it.get("media_id") else None,
                    "start_ms": it.get("start_ms"), "end_ms": it.get("end_ms"),
                    "speaker": it.get("speaker"), "filename": it.get("filename")}
            source_type = "transcript_segment"
        citations.append({"citation_id": cid, "source_type": source_type, "marker": marker, **meta, **mode_meta})

    locate = composed.answer_kind != compose.INSUFFICIENT and _should_locate(decision, composed.answer_kind, body.question)
    term = (getattr(decision, "nav_term", None) or text_terms.specific_term(body.question)) if locate else None
    note = t("messages.occurrences_note", locale).format(term=term) if term else None

    _safe_emit(emit, "done", {"answer_kind": composed.answer_kind, "degraded": degraded,
                              "intent": intent_value, "citations": len(citations)})
    response = {"answer": composed.answer, "answer_kind": composed.answer_kind,
                "intent": intent_value, "degraded": degraded, "claims": claims,
                "citations": citations,
                "citations_used": [c["citation_id"] for c in citations],
                "unsupported_claims": [u["text"] for u in validated["unsupported_claims"]],
                "uncertainties": uncertainties,
                "related_contradictions": related, "knowledge_type": "case",
                "file_cards": file_cards,
                "corrections_pending": corrections_pending,
                "corrections_done": corrections_done,
                "pending_correction": corrections_pending[0] if corrections_pending else None,
                "model_run_id": str(run["id"]), "evidence_count": len(items),
                "occurrences": [], "occurrences_term": term, "occurrences_note": note}
    if answer_key and not degraded and not body.attachments:
        try:
            cache.set_json("answer", answer_key, response, s.ANSWER_CACHE_TTL_SECONDS)
        except Exception:  # noqa: BLE001 — la caché nunca rompe el turno
            log.debug("answer cache write failed", exc_info=True)
    return response


def _should_locate(decision, answer_kind: str, question: str) -> bool:
    from app.services import occurrences
    return occurrences.should_locate(decision, answer_kind, question)
