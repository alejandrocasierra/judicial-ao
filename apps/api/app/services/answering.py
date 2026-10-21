"""RAG con evidencia (SSD Â§17-20, Â§47, Â§98): recuperaciÃ³n lÃ©xica, contexto mÃ­nimo,
aislamiento de contenido no confiable y contrato de respuesta validado."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import logging
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import rows
from app.providers.embeddings import get_embedding_provider
from app.services import abstention, retrieval_crossencoder, retrieval_rank, text_terms
from app.services.intent import Intent, classify
from app.services.retrieval_plan import RetrievalPlan, build as build_plan, replace_queries

log = logging.getLogger(__name__)

# Súbelo cuando cambie la lógica de recuperación/abstención. Sin esto, un
# resultado vacío cacheado sobrevive 10 minutos al arreglo y el usuario sigue
# viendo «no hay evidencia» aunque el bug ya esté corregido.
RETRIEVAL_CACHE_VERSION = "r3-2026.10.21"


def _cache_namespace(case_id: str) -> str:
    return f"retrieval:{case_id}"


def _cache_key(case_id: str, question: str, plan: RetrievalPlan,
               document_ids: list[str] | None, media_ids: list[str] | None,
               ocr_mode: str | None, asr_mode: str | None) -> str:
    """Clave estable: pregunta normalizada + alcance + plan + embeddings."""
    modes = "|".join([
        ocr_mode or "",
        asr_mode or "",
        ",".join(sorted(document_ids or [])),
        ",".join(sorted(media_ids or [])),
    ])
    raw = "|".join([
        RETRIEVAL_CACHE_VERSION,
        str(case_id),
        text_terms.norm(question).strip(),
        modes,
        "\x1f".join(plan.queries),
        str(plan.final_k),
        plan.embedding_model or "",
        plan.embedding_version or "",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_safe(value):
    """UUID/Decimal -> tipos serializables para la cachÃ©."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _cache_get(namespace: str, key: str) -> list[dict] | None:
    try:
        from app.services import cache
        value = cache.get_json(namespace, key)
    except Exception:  # noqa: BLE001 â€” la cachÃ© nunca rompe el turno
        return None
    if isinstance(value, list) and all(isinstance(it, dict) for it in value):
        # Copia defensiva: el LRU en proceso devuelve el mismo objeto y los
        # llamadores renumeran handles sobre los items.
        return copy.deepcopy(value)
    return None


def _cache_set(namespace: str, key: str, items: list[dict], ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    try:
        from app.services import cache
        cache.set_json(namespace, key, _json_safe(items), ttl_seconds)
    except Exception as exc:  # noqa: BLE001 â€” la cachÃ© nunca rompe el turno
        log.debug("retrieval cache write failed: %s", exc)


def _soft_coverage(conn: Connection, case_id: str, question: str) -> float | None:
    """Cobertura lÃ©xica de la pregunta en el expediente. None = no medible."""
    try:
        return abstention.term_coverage(conn, case_id, question)
    except Exception as exc:  # noqa: BLE001 â€” la abstenciÃ³n es una seÃ±al, no una puerta
        log.warning("term coverage failed: %s", exc)
        return None


def _fts_query(queries: list[str] | tuple[str, ...]) -> str:
    """to_tsquery con prefijos y raÃ­ces, para que "pago" encuentre "pagos".

    Con FTS_CONFIG=simple no hay stemming: "pagos" no casa con "pago". Se aÃ±aden
    la forma superficial y la raÃ­z de cada tÃ©rmino (sin stopwords ni reporte) y
    se usa prefijo `:*`. Los nÃºmeros se omiten aquÃ­ (los cubre el vector/legacy).
    """
    parts: list[str] = []
    seen: set[str] = set()
    for query in list(queries)[:4]:
        for term in text_terms.search_terms(query):
            if term[0].isdigit():
                # Identificadores enteros (599, 2834, 001) sÃ­ son tÃ©rminos FTS;
                # los montos con puntos/coma se dejan al vector/legacy.
                digits = re.sub(r"[^0-9]", "", term)
                if 3 <= len(digits) <= 6 and "." not in term and "," not in term:
                    if digits not in seen:
                        seen.add(digits)
                        parts.append(f"{digits}:*")
                continue
            variants = {term, text_terms.stem(term)}
            if term.endswith("s") and len(term) > 4:
                variants.add(term[:-1])
            for variant in variants:
                clean = re.sub(r"[^0-9A-Za-zÃÃ‰ÃÃ“ÃšÃœÃ‘Ã¡Ã©Ã­Ã³ÃºÃ¼Ã±]", "", variant)
                if len(clean) < 3 or clean.lower() in seen:
                    continue
                seen.add(clean.lower())
                parts.append(f"{clean}:*")
    return " | ".join(parts)


def _embed_query(question: str, model: str | None = None) -> list[float]:
    """Embedding de la pregunta con caché, usando el proveedor del modelo del índice."""
    s = get_settings()
    effective_model = model or s.EMBEDDING_MODEL
    key = hashlib.sha256(f"{effective_model}|{text_terms.norm(question)}".encode()).hexdigest()
    try:
        from app.services import cache
        cached = cache.get_json("embedding", key)
        if isinstance(cached, list) and cached and all(isinstance(x, (int, float)) for x in cached):
            return [float(x) for x in cached]
    except Exception:  # noqa: BLE001 — la caché es opcional
        log.debug("embedding cache read failed", exc_info=True)
    from app.providers.embeddings import get_embedding_provider_for_model
    provider = (get_embedding_provider() if effective_model == s.EMBEDDING_MODEL
                else get_embedding_provider_for_model(effective_model, s))
    vector = provider.embed([question])[0]
    try:
        from app.services import cache
        cache.set_json("embedding", key, list(vector), 86400)
    except Exception:  # noqa: BLE001
        log.debug("embedding cache write failed", exc_info=True)
    return vector


def _supplement_unindexed(conn: Connection, case_id: str, question: str,
                         items: list[dict], plan: RetrievalPlan) -> list[dict]:
    """AÃ±ade documentos/medios SIN chunks al resultado hÃ­brido.

    `has_chunks` es global: si el caso tiene un solo chunk (p. ej. el audio), el
    fallback legacy se desactivaba para todos y un PDF sin indexar quedaba
    invisible. AquÃ­ se consulta quÃ© fuentes no tienen ningÃºn chunk y se recuperan
    por FTS legacy, sin duplicar las que sÃ­ estÃ¡n indexadas.
    """
    try:
        missing_docs = [str(r["id"]) for r in rows(conn, """
            SELECT d.id FROM documents d
            WHERE d.case_id = :c
              AND NOT EXISTS (SELECT 1 FROM chunks ch WHERE ch.case_id = :c AND ch.document_id = d.id)
        """, c=case_id)]
        missing_media = [str(r["id"]) for r in rows(conn, """
            SELECT m.id FROM media m
            WHERE m.case_id = :c
              AND NOT EXISTS (SELECT 1 FROM chunks ch WHERE ch.case_id = :c AND ch.media_id = m.id)
        """, c=case_id)]
    except Exception as exc:  # noqa: BLE001 â€” el complemento es opcional
        log.debug("unindexed source check failed: %s", exc)
        return items

    extra: list[dict] = []
    if missing_docs:
        extra += legacy_retrieve(conn, case_id, question, document_ids=missing_docs, plan=plan)
    if missing_media:
        extra += legacy_retrieve(conn, case_id, question, media_ids=missing_media, plan=plan)
    if not extra:
        return items
    # `dedupe` conserva la mejor pÃ¡gina/segmento y evita citar dos veces la misma fuente.
    return retrieval_rank.dedupe(items + extra)


def retrieve(conn: Connection, case_id: str, question: str,
             document_ids: list[str] | None = None, media_ids: list[str] | None = None,
             ocr_mode: str | None = None, asr_mode: str | None = None, *,
             plan: RetrievalPlan | None = None,
             intent: Intent | None = None,
             diagnostics: dict | None = None) -> list[dict]:
    """RecuperaciÃ³n hÃ­brida (FTS + pgvector + RRF) con embudo y rerank.

    Si se pasan document_ids/media_ids (los "@" del chat), la bÃºsqueda se acota a
    esos archivos. `ocr_mode` y `asr_mode` filtran los chunks por el motor que los
    generÃ³ ("todos" o None = sin filtro).

    `plan` e `intent` son opcionales: si no llegan se clasifican/construyen aquÃ­.
    La abstenciÃ³n es BLANDA: cobertura 0 devuelve []; cobertura baja solo penaliza
    el ranking.
    """
    s = get_settings()
    # 0049: la columna search_tsv se genera SIEMPRE con español + unaccent. No se
    # usa FTS_CONFIG aquí para que un env antiguo (simple) no consulte un índice
    # con otra configuración lingüística.
    cfg, n, rrf_k = "spanish", s.RETRIEVAL_SNIPPET_MAX_CHARS, s.RETRIEVAL_RRF_K

    if plan is None:
        decision = classify(question, has_attachments=bool(document_ids or media_ids))
        plan = build_plan(question, decision, settings=s)
        if intent is None:
            intent = decision.intent
    elif intent is None:
        intent = classify(question, has_attachments=bool(document_ids or media_ids)).intent

    namespace = _cache_namespace(case_id)
    key = _cache_key(case_id, question, plan, document_ids, media_ids, ocr_mode, asr_mode)
    # La cobertura se calcula SIEMPRE (tambiÃ©n con cachÃ©): el compositor la
    # necesita para no presentar material irrelevante como respuesta parcial.
    # Se toma el mÃ¡ximo entre la pregunta original y las variantes reescritas:
    # la original evita que los sinÃ³nimos diluyan la seÃ±al; las variantes permiten
    # follow-ups elÃ­pticos ("Â¿y quiÃ©n lo dijo?").
    original_coverage = _soft_coverage(conn, case_id, question)
    rewritten_coverage = _soft_coverage(conn, case_id, " ".join(plan.queries[:3]))
    measured = [c for c in (original_coverage, rewritten_coverage) if c is not None]
    coverage = max(measured) if measured else None
    if diagnostics is not None:
        diagnostics["coverage"] = coverage
    cached = _cache_get(namespace, key)
    if cached is not None:
        return cached

    scoped = bool(document_ids or media_ids)
    scope_params: dict = {}
    scope_sql = ""
    if scoped:
        scope_sql = " AND (c.document_id = ANY(CAST(:dids AS uuid[])) OR c.media_id = ANY(CAST(:mids AS uuid[])))"
        scope_params = {"dids": "{" + ",".join(document_ids or []) + "}",
                        "mids": "{" + ",".join(media_ids or []) + "}"}

    # Filtro por motor OCR/ASR. NULL en metadata = chunks antiguos o corregidos manualmente;
    # los incluimos siempre para no perder evidencia cuando no hay metadata.
    mode_filter_sql = ""
    if ocr_mode and ocr_mode != "todos":
        mode_filter_sql += " AND (c.metadata->>'ocr_mode' IS NULL OR c.metadata->>'ocr_mode' = :ocr_mode)"
        scope_params["ocr_mode"] = ocr_mode
    if asr_mode and asr_mode != "todos":
        mode_filter_sql += " AND (c.metadata->>'asr_mode' IS NULL OR c.metadata->>'asr_mode' = :asr_mode)"
        scope_params["asr_mode"] = asr_mode

    # Fallback a FTS legacy cuando el caso aÃºn no tiene chunks indexados.
    has_chunks = conn.execute(text("SELECT 1 FROM chunks WHERE case_id = :c LIMIT 1"), {"c": case_id}).scalar() is not None
    if not has_chunks:
        items = legacy_retrieve(conn, case_id, question, document_ids=document_ids,
                                media_ids=media_ids, plan=plan)
        _cache_set(namespace, key, items, s.RETRIEVAL_CACHE_TTL_SECONDS)
        return items

    if coverage is not None and coverage <= 0.0:
        return []

    # ExpansiÃ³n por grafo: solo para intenciones de dato/anÃ¡lisis.
    if plan.use_graph and plan.queries:
        try:
            from app.services import graph_expand
            expanded = graph_expand.expand_queries(conn, case_id, question, plan.queries)
            if expanded and tuple(expanded) != plan.queries:
                plan = replace_queries(plan, expanded)
        except Exception as exc:  # noqa: BLE001 â€” el grafo es opcional
            log.warning("graph expansion skipped: %s", exc)

    q_lex = _fts_query(plan.queries)

    fts_results: list[dict] = []
    if q_lex:
        fts_results = rows(conn, """
            SELECT c.id, c.chunk_type, c.document_id, c.page_number, c.media_id,
                   c.start_ms, c.end_ms, left(c.text, :n) AS text, c.metadata,
                   coalesce(d.filename, m.filename) AS filename,
                   ts_rank_cd(c.search_tsv, to_tsquery(CAST(:cfg AS regconfig), :q)) AS score
            FROM chunks c
            LEFT JOIN documents d ON d.id = c.document_id
            LEFT JOIN media m ON m.id = c.media_id
            WHERE c.case_id = :c AND c.search_tsv @@ to_tsquery(CAST(:cfg AS regconfig), :q)/*SCOPE*//*MODE_FILTER*/
            ORDER BY score DESC LIMIT :k
        """.replace("/*SCOPE*/", scope_sql).replace("/*MODE_FILTER*/", mode_filter_sql),
            c=case_id, cfg=cfg, q=q_lex, n=n, k=plan.candidate_k, **scope_params)

    vector_results: list[dict] = []

    effective_model = plan.embedding_model

    def _vector_query(version_filter: str, include_model: bool = True) -> list[dict]:
        params = {**scope_params, "c": case_id, "n": n, "k": plan.candidate_k}
        embedding_filter = ""
        if include_model and effective_model:
            embedding_filter += " AND c.embedding_model = :em"
            params["em"] = effective_model
        if version_filter:
            embedding_filter += " AND c.embedding_version = :ev"
            params["ev"] = plan.embedding_version
        return rows(conn, """
            SELECT c.id, c.chunk_type, c.document_id, c.page_number, c.media_id,
                   c.start_ms, c.end_ms, left(c.text, :n) AS text, c.metadata,
                   coalesce(d.filename, m.filename) AS filename,
                   1 - (c.embedding <=> CAST(:emb AS vector)) AS score
            FROM chunks c
            LEFT JOIN documents d ON d.id = c.document_id
            LEFT JOIN media m ON m.id = c.media_id
            WHERE c.case_id = :c AND c.embedding IS NOT NULL/*SCOPE*//*MODE_FILTER*//*EMBEDDING_FILTER*/
            ORDER BY c.embedding <=> CAST(:emb AS vector)
            LIMIT :k
        """.replace("/*SCOPE*/", scope_sql).replace("/*MODE_FILTER*/", mode_filter_sql)
           .replace("/*EMBEDDING_FILTER*/", embedding_filter), emb=_vector_literal(emb), **params)

    try:
        emb = None
        model_available = True
        if plan.embedding_model:
            models = [r["embedding_model"] for r in rows(conn,
                "SELECT DISTINCT embedding_model FROM chunks WHERE case_id = :c AND embedding_model IS NOT NULL",
                c=case_id) if r.get("embedding_model")]
            model_available = (plan.embedding_model in models) if models else True
            if not model_available:
                # El índice puede estar construido con el fallback local. Se usa el
                # modelo real del índice (un único espacio vectorial) en vez de
                # mezclar o dejar la rama vacía.
                local = next((m for m in models if str(m).startswith("local-ngram")), None)
                if local:
                    effective_model = local
                    model_available = True
                    log.info("embedding_model_local_fallback case=%s configured=%s actual=%s",
                             case_id, plan.embedding_model, local)
                else:
                    log.warning("embedding_model_mismatch case=%s expected=%s available=%s",
                                case_id, plan.embedding_model, models[:5])
        if model_available or s.RETRIEVAL_ALLOW_EMBEDDING_FALLBACK:
            emb = _embed_query(question, effective_model)
        if emb is None:
            vector_results = []
        else:
            strict = bool(plan.strict_embedding_version and plan.embedding_version)
            vector_results = _vector_query(" AND c.embedding_version = :ev" if strict else "")
            if strict and not vector_results:
                log.info("embedding_version_fallback case=%s version=%s", case_id, plan.embedding_version)
                vector_results = _vector_query("")
            if not vector_results and effective_model:
                log.warning("embedding_model_mismatch case=%s expected=%s", case_id, effective_model)
                if s.RETRIEVAL_ALLOW_EMBEDDING_FALLBACK:
                    vector_results = _vector_query("", include_model=False)
    except Exception as exc:
        log.warning("vector search failed: %s", exc)

    items = _rrf_fuse([fts_results, vector_results], k=plan.candidate_k, rrf_k=rrf_k)
    for it in items:
        _normalize_retrieved_item(it)
    # Los chunks estructurados sin fuente primaria no son citables: no compiten
    # por el top-k ni llegan al prompt (auditoría P1 #14).
    items = [it for it in items if it.get("source_type") != "non_citable"]

    items = _supplement_unindexed(conn, case_id, question, items, plan)
    items = retrieval_rank.apply(question, items, plan=plan, intent=intent, coverage=coverage)

    if s.RERANK_CROSSENCODER and plan.enable_rerank and items:
        try:
            items = retrieval_crossencoder.rerank(question, items, plan.final_k)
        except Exception as exc:  # noqa: BLE001 â€” reranker opcional
            log.warning("cross-encoder rerank skipped: %s", exc)

    items = items[:plan.final_k]
    for i, it in enumerate(items, 1):
        it["handle"] = f"E{i}"

    _cache_set(namespace, key, items, s.RETRIEVAL_CACHE_TTL_SECONDS)
    return items


def merge_items(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """Une dos listas de evidencia (p. ej. acotada por "@" + global), deduplicando por
    fuente y renumerando los handles E1..Ek. Los Ã­tems de `primary` van primero."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for it in primary + secondary:
        key = (str(it.get("document_id")), it.get("page_number")) if it.get("document_id") \
            else (str(it.get("segment_id") or it.get("media_id")), it.get("start_ms"))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    for i, it in enumerate(out, 1):
        it["handle"] = f"E{i}"
    return out


def legacy_retrieve(conn: Connection, case_id: str, question: str, k: int | None = None,
                    document_ids: list[str] | None = None, media_ids: list[str] | None = None, *,
                    plan: RetrievalPlan | None = None) -> list[dict]:
    """RecuperaciÃ³n lÃ©xica sobre document_pages/transcript_segments (transiciÃ³n hasta que haya chunks).
    Con document_ids/media_ids (los "@" del chat) la bÃºsqueda se acota a esos archivos."""
    # AbstenciÃ³n BLANDA tambiÃ©n en el fallback: solo se corta con cobertura 0.
    coverage = _soft_coverage(conn, case_id, question)
    if coverage is not None and coverage <= 0.0:
        return []
    s = get_settings()
    cfg, n = "spanish", s.RETRIEVAL_SNIPPET_MAX_CHARS
    k = k or (plan.final_k if plan is not None else s.RETRIEVAL_TOP_K)
    q = _fts_query(list(plan.queries) if plan is not None else [question])
    if not q:
        return []
    params: dict = {"c": case_id, "cfg": cfg, "q": q, "n": n, "k": k}
    branches: list[str] = []
    if not media_ids:  # sin filtro de video: incluye documentos (acotados si hay document_ids)
        doc_filter = ""
        if document_ids:
            doc_filter = " AND d.id = ANY(CAST(:dids AS uuid[]))"
            params["dids"] = "{" + ",".join(document_ids) + "}"
        branches.append("""
        SELECT 'document_page' AS source_type, p.document_id, p.page_number, p.folio, d.filename, NULL::uuid AS media_id,
               NULL::uuid AS segment_id, NULL::bigint AS start_ms, NULL::bigint AS end_ms, NULL AS speaker,
               left(p.text, :n) AS text, ts_rank_cd(p.search_tsv, to_tsquery(CAST(:cfg AS regconfig), :q)) AS score
        FROM document_pages p JOIN documents d ON d.id = p.document_id
        WHERE d.case_id = :c AND p.search_tsv @@ to_tsquery(CAST(:cfg AS regconfig), :q)/*DOC_FILTER*/""".replace("/*DOC_FILTER*/", doc_filter))
    if not document_ids:  # sin filtro de documentos: incluye transcripciones (acotadas si hay media_ids)
        media_filter = ""
        if media_ids:
            media_filter = " AND m.id = ANY(CAST(:mids AS uuid[]))"
            params["mids"] = "{" + ",".join(media_ids) + "}"
        branches.append("""
        SELECT 'transcript_segment', NULL, NULL, NULL, m.filename, s.media_id, s.id, s.start_ms, s.end_ms, sp.label,
               left(s.text, :n), ts_rank_cd(s.search_tsv, to_tsquery(CAST(:cfg AS regconfig), :q))
        FROM transcript_segments s JOIN media m ON m.id = s.media_id LEFT JOIN speakers sp ON sp.id = s.speaker_id
        WHERE m.case_id = :c AND s.search_tsv @@ to_tsquery(CAST(:cfg AS regconfig), :q)/*MEDIA_FILTER*/""".replace("/*MEDIA_FILTER*/", media_filter))
    if not branches:
        return []
    items = rows(conn, "SELECT * FROM (" + " UNION ALL ".join(branches) + ") r ORDER BY score DESC LIMIT :k", **params)
    for i, it in enumerate(items, 1):
        it["handle"] = f"E{i}"
        it["start_mmss"] = text_terms.mmss(it.get("start_ms")) if it.get("start_ms") is not None else ""
        it.setdefault("rerank_score", it.get("score") or 0.0)
    return items


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


def _rrf_fuse(lists: list[list[dict]], k: int, rrf_k: int) -> list[dict]:
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    for lst in lists:
        for rank, it in enumerate(lst, start=1):
            cid = str(it["id"])
            by_id[cid] = it
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [by_id[cid] for cid, _ in ranked[:k]]


def _normalize_retrieved_item(it: dict) -> None:
    meta = it.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    it["metadata"] = meta
    if it.get("document_id"):
        it["source_type"] = "document_page"
        it["folio"] = meta.get("folio") or it.get("page_number")
        it["segment_id"] = None
        it["speaker"] = None
        it["start_mmss"] = ""
    elif it.get("media_id"):
        it["source_type"] = "transcript_segment"
        it["document_id"] = None
        it["page_number"] = None
        it["folio"] = None
        it["segment_id"] = meta.get("segment_id")
        it["speaker"] = meta.get("speaker") or meta.get("speaker_name")
        it["start_mmss"] = text_terms.mmss(it.get("start_ms")) if it.get("start_ms") is not None else ""
    else:
        # Chunk estructurado (claim/event/fact) sin fuente primaria resoluble:
        # no puede citarse, así que no debe llegar al prompt ni al compositor.
        it["source_type"] = "non_citable"
        it["document_id"] = None
        it["page_number"] = None
        it["media_id"] = None
        it["segment_id"] = None
        it["start_mmss"] = ""
    it["filename"] = it.get("filename")
    it["ocr_mode"] = meta.get("ocr_mode")
    it["asr_mode"] = meta.get("asr_mode")
    # El score original se conserva; el rerank aÃ±ade su propia mÃ©trica.
    try:
        it["rerank_score"] = float(it.get("rerank_score") if it.get("rerank_score") is not None
                                   else (it.get("score") or 0.0))
    except (TypeError, ValueError):
        it["rerank_score"] = 0.0


def _source_label(it: dict) -> str:
    if it["source_type"] == "document_page":
        folio = f" folio:{it['folio']}" if it.get("folio") else ""
        return f"document:{it.get('filename') or it['document_id']} page:{it['page_number']}{folio}"
    start = it.get("start_ms")
    mmss = ""
    if start is not None:
        total = int(start) // 1000
        mmss = f" {total // 60:02d}:{total % 60:02d}"
    return (f"video:{it.get('filename') or it['media_id']} minuto{mmss} "
            f"speaker:{it.get('speaker') or 'unknown'}")


def build_user_prompt(question: str, items: list[dict], history: list[dict] | None = None,
                      deep: bool = False) -> str:
    """El contenido del expediente se escapa (html.escape) para que un texto malicioso
    no pueda cerrar la etiqueta <evidence> ni inyectar etiquetas de control."""
    blocks = [f'<evidence id="{it["handle"]}" source="{html.escape(_source_label(it), quote=True)}">\n'
              f'{html.escape(it["text"], quote=False)}\n</evidence>' for it in items]
    parts: list[str] = []
    convo = _history_block(history)
    if convo:
        parts.append(f"<conversation_history>\n{html.escape(convo, quote=False)}\n</conversation_history>")
    if deep:
        parts.append("<analysis_mode>exhaustive</analysis_mode>")
    parts.append("<question>\n" + html.escape(question, quote=False) + "\n</question>")
    parts.append("\n".join(blocks))
    return "\n\n".join(parts)


def _history_block(history: list[dict] | None, max_turns: int = 6,
                   max_chars: int = 2400) -> str:
    """Últimos turnos en texto plano, acotados: resuelven «lo anterior» sin inflar el prompt."""
    lines: list[str] = []
    for turn in (history or [])[-max_turns:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "")
        content = " ".join(str(turn.get("content") or "").split())
        if not content:
            continue
        lines.append(f"{'USUARIO' if role == 'user' else 'ASISTENTE'}: {content[:600]}")
    text = "\n".join(lines)
    return text[-max_chars:]


def system_prompt(locale: str, deep: bool = False) -> tuple[str, str, str]:
    s = get_settings()
    path = s.path(s.PROMPTS_DIR)
    target = path / "answer_question.v2.md"
    if deep and (path / "answer_question.v3.md").exists():
        target = path / "answer_question.v3.md"
    if not target.exists():
        target = path / "answer_question.v1.md"
    raw = target.read_text(encoding="utf-8")
    meta, body = raw.split("---", 2)[1], raw.split("---", 2)[2]
    pid = re.search(r"prompt_id:\s*(\S+)", meta).group(1)
    ver = re.search(r"version:\s*(\S+)", meta).group(1)
    rendered = body.strip().replace("{{LOCALE}}", locale)
    if deep:
        rendered = (rendered.replace("{{MAX_CLAIMS}}", str(s.DEEP_MAX_CLAIMS))
                    .replace("{{MAX_WORDS}}", "900"))
    return rendered, pid, ver


_WORD = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]{3,}", re.U)

# Palabras vacÃ­as o de reporte que no aportan contenido verificable. Se excluyen del
# solapamiento para no rechazar frases correctas tipo Â«segÃºn las marcas de tiempo,
# hablÃ³ en los minutos 01:26, 01:28â€¦Â». Los NÃšMEROS siguen siendo obligatorios.
_STOP = {
    "los", "las", "del", "que", "con", "por", "para", "una", "uno", "unos", "unas", "este", "esta",
    "estos", "estas", "como", "sobre", "entre", "desde", "hasta", "segun", "marca", "marcas", "tiempo",
    "tiempos", "minuto", "minutos", "hora", "horas", "intervencion", "intervenciones", "atribuida",
    "atribuidas", "hablo", "habla", "dice", "dijo", "dicen", "menciona", "menciono", "consta", "aparece",
    "registra", "senala", "mas", "menos", "respecto", "conforme", "indica", "indican", "corresponde",
    "corresponden", "evidencia", "evidencias", "fuente", "fuentes", "fue", "fueron", "ser",
    "estan", "sido", "cuando", "donde", "cual", "cuales", "quien", "quienes", "al", "se",
}


def _norm(t: str) -> str:
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(ch))


def grounding(claim: str, evidence: str) -> tuple[float, list[str]]:
    """ProporciÃ³n de tÃ©rminos de la afirmaciÃ³n presentes en la evidencia citada, y nÃºmeros ausentes.
    Un nÃºmero (monto, fecha, folio, minuto) que no aparece en la evidencia es la alucinaciÃ³n mÃ¡s daÃ±ina
    en lo jurÃ­dico y se exige siempre."""
    ev = set(_WORD.findall(_norm(evidence)))
    ev_nums = {re.sub(r"[.,]", "", w) for w in ev if w[0].isdigit()}
    toks = [w for w in _WORD.findall(_norm(claim)) if w not in _STOP]
    if not toks:
        return 1.0, []
    missing_nums = [w for w in toks if w[0].isdigit() and re.sub(r"[.,]", "", w) not in ev_nums]
    words = [w for w in toks if not w[0].isdigit()]
    ratio = (sum(1 for w in words if w in ev) / len(words)) if words else 1.0
    return ratio, missing_nums


def _evidence_context(it: dict) -> str:
    """Texto de la evidencia + su contexto (archivo, minuto, hablante, pÃ¡gina, folio).

    El modelo cita el minuto y el hablante (Â«minuto 01:58 Â· Ãlvaro LÃºzico ÃlvarezÂ»),
    pero esos datos viven en la ETIQUETA de la evidencia, no en el texto. Sin incluirlos
    aquÃ­, el validador de grounding descartaba esas afirmaciones correctas."""
    parts = [it.get("text") or ""]
    for key in ("filename", "speaker", "start_mmss", "page_number", "folio", "name"):
        v = it.get(key)
        if v:
            parts.append(str(v))
    return " ".join(parts)


def parse_and_validate(raw: str, items: list[dict], min_overlap: float = 0.0) -> dict:
    """Answer contract (SSD Â§20.2). AfirmaciÃ³n sin cita vÃ¡lida => unsupported_claims, nunca a la respuesta."""
    handles = {it["handle"] for it in items}
    by_handle = {it["handle"]: it for it in items}
    # Calibración: 0.6 descartaba paráfrasis correctas. El set dorado y el
    # compositor usan 0.34; se fija como techo para no volver a sobre-abstenerse
    # aunque un entorno antiguo conserve 0.6.
    min_overlap = min(float(min_overlap), 0.34)
    txt = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        data = json.loads(txt)
        assert isinstance(data, dict)
    except Exception:
        return {"claims": [], "unsupported_claims": [], "uncertainties": [], "schema_valid": False}
    supported, unsupported = [], []
    for c in data.get("claims") or []:
        if not isinstance(c, dict) or not isinstance(c.get("text"), str):
            continue
        cites = [x for x in (c.get("citations") or []) if isinstance(x, str)]
        if not cites or not set(cites) <= handles:
            unsupported.append({"text": c["text"], "citations": cites, "reason": "invalid_citation"})
            continue
        cited_text = " ".join(_evidence_context(by_handle[h]) for h in cites)
        ratio, missing_nums = grounding(c["text"], cited_text)
        if ratio < min_overlap or missing_nums:
            unsupported.append({"text": c["text"], "citations": cites, "reason": "not_grounded",
                                "overlap": round(ratio, 3), "missing_numbers": missing_nums})
            continue
        supported.append({"text": c["text"], "citations": cites})
    unc = [u for u in (data.get("uncertainties") or []) if isinstance(u, str)]
    return {"claims": supported, "unsupported_claims": unsupported, "uncertainties": unc, "schema_valid": True}


def input_hash(question: str, items: list[dict]) -> str:
    h = hashlib.sha256(question.encode())
    for it in items:
        h.update(str(it.get("document_id") or it.get("segment_id")).encode())
        h.update(str(it.get("page_number")).encode())
    return h.hexdigest()

