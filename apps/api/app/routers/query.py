"""POST /v1/cases/{id}/query — RAG o agente con herramientas y verificación."""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.core.i18n import negotiate, t
from app.providers.llm import get_llm
from app.schemas import QueryIn
from app.security.deps import Principal, case_access, current_principal
from app.services import agent, answering, audit, case_tools, metrics, query_hints, ratelimit

router = APIRouter(prefix="/cases/{case_id}", tags=["ai"])


def _insufficient(case_id: UUID, body: QueryIn, request: Request, p: Principal, locale: str) -> dict:
    with tx(p.org_id, p.user_id) as c:
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="ai.query", entity_type="case",
                     entity_id=str(case_id), after={"mode": body.mode, "strategy": body.strategy, "evidence": 0}, request=request)
    return {"answer": t("messages.insufficient_evidence", locale), "claims": [], "citations": [],
            "unsupported_claims": [], "uncertainties": [t("messages.insufficient_evidence", locale)],
            "related_contradictions": [], "knowledge_type": "case", "file_cards": [], "evidence_count": 0}


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


@router.post("/query")
def query(case_id: UUID, body: QueryIn, request: Request, p: Principal = Depends(current_principal)):
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
        hints = query_hints.merge(query_hints.hint(body.question))
        return _run_query(case_id, body, request, p, locale, case, s, hints=hints)


def _run_query(case_id: UUID, body: QueryIn, request: Request, p: Principal, locale: str, case: dict, s,
               history: list[dict] | None = None, hints: list[str] | None = None) -> dict:
    from app.providers.llm import get_llm_for_model

    llm = get_llm_for_model(str(body.model_id), p.org_id, p.user_id) if body.model_id else get_llm()
    file_cards: list[dict] = []
    corrections_pending: list[dict] = []
    corrections_done: list[dict] = []

    if body.strategy == "agent":
        agent_prompt = None
        if body.agent_id:
            with tx(p.org_id, p.user_id) as c:
                # System prompt del agente + system prompts de sus skills enlazadas.
                agent_prompt = agent.load_agent_prompt(c, str(body.agent_id))
        with tx(p.org_id, p.user_id) as c:
            agent_result = agent.run_agent_query(c, str(case_id), body.question, locale,
                                                 agent_prompt=agent_prompt, llm=llm,
                                                 attachments=_attachment_dicts(body),
                                                 org_id=str(p.org_id), actor_id=str(p.user_id),
                                                 history_turns=history, hints=hints)
        # Propagación diferida por las tools de escritura (reindex pgvector, rebuild del
        # grafo, encolado de jobs): solo DESPUÉS de confirmar la transacción del loop.
        case_tools.drain_actions(agent_result.pop("post_commit", []))
        items = agent_result.get("evidence", [])
        file_cards = agent_result.get("file_cards", [])
        corrections_pending = agent_result.get("corrections_pending", [])
        corrections_done = agent_result.get("corrections_done", [])
        if not items:
            if not file_cards and not corrections_pending:
                return _insufficient(case_id, body, request, p, locale)
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
            return {"answer": answer, "claims": [], "citations": [], "unsupported_claims": [],
                    "uncertainties": [], "related_contradictions": [], "knowledge_type": "case",
                    "file_cards": file_cards, "corrections_pending": corrections_pending,
                    "corrections_done": corrections_done,
                    "pending_correction": corrections_pending[0] if corrections_pending else None,
                    "evidence_count": 0}
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
        with tx(p.org_id, p.user_id) as c:
            items = answering.retrieve(c, str(case_id), body.question)
            if body.attachments:
                # Los "@" acotan una primera pasada y se fusionan (primero lo adjunto, luego lo global).
                doc_ids = [str(a.id) for a in body.attachments if a.kind == "document"]
                media_ids = [str(a.id) for a in body.attachments if a.kind == "media"]
                scoped = answering.retrieve(c, str(case_id), body.question,
                                            document_ids=doc_ids or None, media_ids=media_ids or None)
                items = answering.merge_items(scoped, items)
                file_cards = _attachment_file_cards(c, case_id, body)
        if not items:
            return _insufficient(case_id, body, request, p, locale)
        system, prompt_id, prompt_version = answering.system_prompt(locale)
        result = llm.complete(system, answering.build_user_prompt(body.question, items))
        validated = answering.parse_and_validate(result.text, items, s.ANSWER_MIN_GROUNDING_OVERLAP)
        task = "answer_question"
        raw_text = result.text

    by_handle = {it["handle"]: it for it in items}

    with tx(p.org_id, p.user_id) as c:
        run = one(c, """INSERT INTO model_runs (organization_id, case_id, task, provider, model, prompt_id, prompt_version,
              pipeline_version, input_hash, output, validation, tokens_in, tokens_out, actor_id)
            VALUES (:o,:c,:task,:pr,:m,:pid,:pv,:plv,:ih,CAST(:out AS jsonb),CAST(:val AS jsonb),:ti,:to,:u) RETURNING id""",
                  o=p.org_id, c=str(case_id), task=task, pr=result.provider, m=result.model, pid=prompt_id, pv=prompt_version,
                  plv=s.PIPELINE_VERSION, ih=answering.input_hash(body.question, items), out=json.dumps({"raw_len": len(raw_text)}),
                  val=json.dumps({"schema_valid": validated["schema_valid"], "unsupported": len(validated["unsupported_claims"])}),
                  ti=result.tokens_in, to=result.tokens_out, u=p.user_id)
        c.execute(text("UPDATE cases SET spent_llm_tokens = spent_llm_tokens + :n WHERE id = :c"),
                  {"n": result.tokens_in + result.tokens_out, "c": str(case_id)})
        handle_to_cit: dict[str, str] = {}
        for h in sorted({h for cl in validated["claims"] for h in cl["citations"]}):
            it = by_handle[h]
            if it["source_type"] == "document_page":
                cid = one(c, """INSERT INTO citations (organization_id, case_id, target_type, target_id, source_type, document_id, page_number, folio)
                    VALUES (:o,:c,'answer',:r,'document_page',:d,:pg,:f) RETURNING id""",
                          o=p.org_id, c=str(case_id), r=run["id"], d=str(it["document_id"]), pg=it["page_number"], f=it["folio"])["id"]
            elif it["source_type"] == "transcript_segment":
                cid = one(c, """INSERT INTO citations (organization_id, case_id, target_type, target_id, source_type, media_id, segment_id, start_ms, end_ms)
                    VALUES (:o,:c,'answer',:r,'transcript_segment',:m,:sg,:s,:e) RETURNING id""",
                          o=p.org_id, c=str(case_id), r=run["id"], m=str(it["media_id"]), sg=str(it["segment_id"]),
                          s=it["start_ms"], e=it["end_ms"])["id"]
            else:
                continue
            handle_to_cit[h] = str(cid)
        pages = [(str(i["document_id"]), i["page_number"]) for i in items if i["source_type"] == "document_page"]
        segs = [str(i["segment_id"]) for i in items if i["source_type"] == "transcript_segment"]
        related = rows(c, """SELECT DISTINCT co.id, co.contradiction_type, co.description, co.severity FROM contradictions co
            JOIN citations ci ON ci.target_type='claim' AND ci.target_id IN (co.claim_a_id, co.claim_b_id)
            WHERE co.case_id = :c AND (ci.segment_id = ANY(CAST(:segs AS uuid[]))
               OR (ci.document_id::text || ':' || ci.page_number) = ANY(CAST(:pages AS text[])))""",
                       c=str(case_id), segs="{" + ",".join(segs) + "}", pages="{" + ",".join(f'"{d}:{n}"' for d, n in pages) + "}")
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="ai.query", entity_type="case", entity_id=str(case_id),
                     after={"mode": body.mode, "strategy": body.strategy, "model_run_id": str(run["id"]), "evidence": len(items),
                            "unsupported": len(validated["unsupported_claims"])}, request=request)

    # Métricas de observabilidad (SSD §28)
    metrics.track_llm_usage(p.org_id, result.provider, result.model, task, result.tokens_in, result.tokens_out, 0.0)
    if validated["claims"]:
        cited = sum(1 for cl in validated["claims"] if cl["citations"])
        metrics.update_citation_accuracy(p.org_id, str(case_id), cited / len(validated["claims"]))
    metrics.update_retrieval_hit_rate(p.org_id, str(case_id), len(handle_to_cit) / len(items) if items else 0.0)

    claims = [{"text": cl["text"], "citations": [handle_to_cit[h] for h in cl["citations"] if h in handle_to_cit]} for cl in validated["claims"]]
    uncertainties = list(validated["uncertainties"])
    if validated["unsupported_claims"]:
        uncertainties.append(t("messages.unsupported_claims_removed", locale))
    if related:
        uncertainties.append(t("messages.contradiction_requires_review", locale))
    citations = []
    for h, cid in handle_to_cit.items():
        it = by_handle[h]
        citations.append({"citation_id": cid, "source_type": it["source_type"],
                          **({"document_id": str(it["document_id"]), "page": it["page_number"], "folio": it["folio"], "filename": it["filename"]}
                             if it["source_type"] == "document_page" else
                             {"media_id": str(it["media_id"]), "start_ms": it["start_ms"], "end_ms": it["end_ms"],
                              "speaker": it["speaker"], "filename": it.get("filename")})})
    answer = " ".join(cl["text"] for cl in claims)
    if not answer and items:
        # Hay evidencia, pero la síntesis no produjo frases verificables (o el modelo
        # varió su estrategia). En vez de decir «sin evidencia», se muestran las fuentes
        # ENCONTRADAS con su cita (documento+página o video+minuto+hablante).
        snips = []
        for it in items[:3]:
            if it.get("source_type") == "document_page":
                src = f"{it.get('filename')} · página {it.get('page_number')}"
            else:
                src = f"{it.get('filename')} · {it.get('start_mmss') or it.get('start_ms')} · {it.get('speaker') or ''}".strip()
            snips.append(f"- «{(it.get('text') or '').strip()[:280]}» — {src}")
        answer = t("messages.evidence_fallback", locale) + "\n" + "\n".join(snips)
    if not answer:
        answer = t("messages.insufficient_evidence", locale)
    return {"answer": answer, "claims": claims, "citations": citations,
            "unsupported_claims": [u["text"] for u in validated["unsupported_claims"]], "uncertainties": uncertainties,
            "related_contradictions": related, "knowledge_type": "case", "file_cards": file_cards,
            "corrections_pending": corrections_pending, "corrections_done": corrections_done,
            "pending_correction": corrections_pending[0] if corrections_pending else None,
            "model_run_id": str(run["id"]), "evidence_count": len(items)}
