"""RAG con evidencia (SSD §17-20, §47, §98): recuperación léxica, contexto mínimo,
aislamiento de contenido no confiable y contrato de respuesta validado."""
from __future__ import annotations

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
from app.services import abstention

log = logging.getLogger(__name__)


def retrieve(conn: Connection, case_id: str, question: str,
             document_ids: list[str] | None = None, media_ids: list[str] | None = None) -> list[dict]:
    """Recuperación híbrida (FTS + pgvector + RRF). Si se pasan document_ids/media_ids
    (los "@" del chat), la búsqueda se acota a esos archivos."""
    s = get_settings()
    cfg, k, n, rrf_k = s.FTS_CONFIG, s.RETRIEVAL_TOP_K, s.RETRIEVAL_SNIPPET_MAX_CHARS, s.RETRIEVAL_RRF_K

    if abstention.should_abstain(conn, case_id, question):
        return []

    scoped = bool(document_ids or media_ids)
    scope_params: dict = {}
    scope_sql = ""
    if scoped:
        scope_sql = " AND (c.document_id = ANY(CAST(:dids AS uuid[])) OR c.media_id = ANY(CAST(:mids AS uuid[])))"
        scope_params = {"dids": "{" + ",".join(document_ids or []) + "}",
                        "mids": "{" + ",".join(media_ids or []) + "}"}

    # Fallback a FTS legacy cuando el caso aún no tiene chunks indexados.
    has_chunks = conn.execute(text("SELECT 1 FROM chunks WHERE case_id = :c LIMIT 1"), {"c": case_id}).scalar() is not None
    if not has_chunks:
        return legacy_retrieve(conn, case_id, question, document_ids=document_ids, media_ids=media_ids)

    q_lex = rows(conn, "SELECT string_agg(quote_literal(lexeme), ' | ') AS q FROM unnest(to_tsvector(CAST(:cfg AS regconfig), :t)) "
                         "WHERE length(lexeme) >= 3", cfg=cfg, t=question)[0]["q"]

    fts_results: list[dict] = []
    if q_lex:
        fts_results = rows(conn, """
            SELECT c.id, c.chunk_type, c.document_id, c.page_number, c.media_id,
                   c.start_ms, c.end_ms, left(c.text, :n) AS text, c.metadata,
                   d.filename, ts_rank_cd(c.tsv, to_tsquery(CAST(:cfg AS regconfig), :q)) AS score
            FROM chunks c
            LEFT JOIN documents d ON d.id = c.document_id
            WHERE c.case_id = :c AND c.tsv @@ to_tsquery(CAST(:cfg AS regconfig), :q)/*SCOPE*/
            ORDER BY score DESC LIMIT :k
        """.replace("/*SCOPE*/", scope_sql), c=case_id, cfg=cfg, q=q_lex, n=n, k=k, **scope_params)

    vector_results: list[dict] = []
    try:
        emb = get_embedding_provider().embed([question])[0]
        vector_results = rows(conn, """
            SELECT c.id, c.chunk_type, c.document_id, c.page_number, c.media_id,
                   c.start_ms, c.end_ms, left(c.text, :n) AS text, c.metadata,
                   d.filename, 1 - (c.embedding <=> CAST(:emb AS vector)) AS score
            FROM chunks c
            LEFT JOIN documents d ON d.id = c.document_id
            WHERE c.case_id = :c AND c.embedding IS NOT NULL/*SCOPE*/
            ORDER BY c.embedding <=> CAST(:emb AS vector)
            LIMIT :k
        """.replace("/*SCOPE*/", scope_sql), c=case_id, n=n, k=k, emb=_vector_literal(emb), **scope_params)
    except Exception as exc:
        log.warning("vector search failed: %s", exc)

    items = _rrf_fuse([fts_results, vector_results], k=k, rrf_k=rrf_k)
    for i, it in enumerate(items, 1):
        it["handle"] = f"E{i}"
        _normalize_retrieved_item(it)
    return items


def merge_items(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """Une dos listas de evidencia (p. ej. acotada por "@" + global), deduplicando por
    fuente y renumerando los handles E1..Ek. Los ítems de `primary` van primero."""
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
                    document_ids: list[str] | None = None, media_ids: list[str] | None = None) -> list[dict]:
    """Recuperación léxica sobre document_pages/transcript_segments (transición hasta que haya chunks).
    Con document_ids/media_ids (los "@" del chat) la búsqueda se acota a esos archivos."""
    if abstention.should_abstain(conn, case_id, question):
        return []
    s = get_settings()
    cfg, k, n = s.FTS_CONFIG, k or s.RETRIEVAL_TOP_K, s.RETRIEVAL_SNIPPET_MAX_CHARS
    q = rows(conn, "SELECT string_agg(quote_literal(lexeme), ' | ') AS q FROM unnest(to_tsvector(CAST(:cfg AS regconfig), :t)) "
                   "WHERE length(lexeme) >= 3", cfg=cfg, t=question)[0]["q"]
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
               left(p.text, :n) AS text, ts_rank_cd(p.tsv, to_tsquery(CAST(:cfg AS regconfig), :q)) AS score
        FROM document_pages p JOIN documents d ON d.id = p.document_id
        WHERE d.case_id = :c AND p.tsv @@ to_tsquery(CAST(:cfg AS regconfig), :q)/*DOC_FILTER*/""".replace("/*DOC_FILTER*/", doc_filter))
    if not document_ids:  # sin filtro de documentos: incluye transcripciones (acotadas si hay media_ids)
        media_filter = ""
        if media_ids:
            media_filter = " AND m.id = ANY(CAST(:mids AS uuid[]))"
            params["mids"] = "{" + ",".join(media_ids) + "}"
        branches.append("""
        SELECT 'transcript_segment', NULL, NULL, NULL, m.filename, s.media_id, s.id, s.start_ms, s.end_ms, sp.label,
               left(s.text, :n), ts_rank_cd(s.tsv, to_tsquery(CAST(:cfg AS regconfig), :q))
        FROM transcript_segments s JOIN media m ON m.id = s.media_id LEFT JOIN speakers sp ON sp.id = s.speaker_id
        WHERE m.case_id = :c AND s.tsv @@ to_tsquery(CAST(:cfg AS regconfig), :q)/*MEDIA_FILTER*/""".replace("/*MEDIA_FILTER*/", media_filter))
    if not branches:
        return []
    items = rows(conn, "SELECT * FROM (" + " UNION ALL ".join(branches) + ") r ORDER BY score DESC LIMIT :k", **params)
    for i, it in enumerate(items, 1):
        it["handle"] = f"E{i}"
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
    if it.get("document_id"):
        it["source_type"] = "document_page"
        it["folio"] = meta.get("folio") or it.get("page_number")
        it["segment_id"] = None
        it["speaker"] = None
    else:
        it["source_type"] = "transcript_segment"
        it["document_id"] = None
        it["page_number"] = None
        it["folio"] = None
        it["segment_id"] = meta.get("segment_id")
        it["speaker"] = meta.get("speaker")
    it["filename"] = it.get("filename")


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


def build_user_prompt(question: str, items: list[dict]) -> str:
    """El contenido del expediente se escapa (html.escape) para que un texto malicioso
    no pueda cerrar la etiqueta <evidence> ni inyectar etiquetas de control."""
    blocks = [f'<evidence id="{it["handle"]}" source="{html.escape(_source_label(it), quote=True)}">\n'
              f'{html.escape(it["text"], quote=False)}\n</evidence>' for it in items]
    return ("<question>\n" + html.escape(question, quote=False) + "\n</question>\n\n" + "\n".join(blocks))


def system_prompt(locale: str) -> tuple[str, str, str]:
    s = get_settings()
    raw = (s.path(s.PROMPTS_DIR) / "answer_question.v1.md").read_text(encoding="utf-8")
    meta, body = raw.split("---", 2)[1], raw.split("---", 2)[2]
    pid = re.search(r"prompt_id:\s*(\S+)", meta).group(1)
    ver = re.search(r"version:\s*(\S+)", meta).group(1)
    return body.strip().replace("{{LOCALE}}", locale), pid, ver


_WORD = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]{3,}", re.U)

# Palabras vacías o de reporte que no aportan contenido verificable. Se excluyen del
# solapamiento para no rechazar frases correctas tipo «según las marcas de tiempo,
# habló en los minutos 01:26, 01:28…». Los NÚMEROS siguen siendo obligatorios.
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
    """Proporción de términos de la afirmación presentes en la evidencia citada, y números ausentes.
    Un número (monto, fecha, folio, minuto) que no aparece en la evidencia es la alucinación más dañina
    en lo jurídico y se exige siempre."""
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
    """Texto de la evidencia + su contexto (archivo, minuto, hablante, página, folio).

    El modelo cita el minuto y el hablante («minuto 01:58 · Álvaro Lúzico Álvarez»),
    pero esos datos viven en la ETIQUETA de la evidencia, no en el texto. Sin incluirlos
    aquí, el validador de grounding descartaba esas afirmaciones correctas."""
    parts = [it.get("text") or ""]
    for key in ("filename", "speaker", "start_mmss", "page_number", "folio", "name"):
        v = it.get(key)
        if v:
            parts.append(str(v))
    return " ".join(parts)


def parse_and_validate(raw: str, items: list[dict], min_overlap: float = 0.0) -> dict:
    """Answer contract (SSD §20.2). Afirmación sin cita válida => unsupported_claims, nunca a la respuesta."""
    handles = {it["handle"] for it in items}
    by_handle = {it["handle"]: it for it in items}
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
