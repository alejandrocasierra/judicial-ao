"""Plan de recuperación por turno (contrato §1.3).

`build()` es puro salvo por `get_settings()`: no toca la base de datos ni el LLM.
Traduce la intención ya clasificada en un plan explícito (consultas, tamaño de
candidatos, cuotas, filtros de embeddings) que `answering.retrieve` consume sin
volver a decidir nada.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import PLACEHOLDERS, Settings, get_settings
from app.services import query_rewrite, text_terms
from app.services.intent import Intent, IntentDecision

# Tope de variantes de búsqueda que viajan al SQL / al grafo.
MAX_QUERIES = 8


@dataclass(frozen=True)
class RetrievalPlan:
    question: str
    queries: tuple[str, ...]
    candidate_k: int
    final_k: int
    doc_quota: float
    max_per_file: int
    max_per_page: int
    enable_rerank: bool
    expand_neighbors: bool
    use_graph: bool
    embedding_model: str | None
    embedding_version: str | None
    strict_embedding_version: bool


def _merge_queries(question: str, variants: list[str]) -> tuple[str, ...]:
    """Original primero, sin duplicados normalizados y con tope."""
    out: list[str] = [question]
    out.extend(variants)
    out.extend(text_terms.expand_query(question))
    seen: set[str] = set()
    merged: list[str] = []
    for variant in out:
        text = " ".join((variant or "").split())
        key = text_terms.norm(text).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return tuple(merged[:MAX_QUERIES])


def build(question: str, decision: IntentDecision, *,
          settings: Settings | None = None,
          history: list[dict] | None = None) -> RetrievalPlan:
    s = settings or get_settings()
    final_k = int(s.RETRIEVAL_FINAL_K)
    candidate_k = max(int(s.RETRIEVAL_CANDIDATE_K), 4 * final_k)
    intent = decision.intent

    rewritten = query_rewrite.rewrite(question, history=history)
    queries = _merge_queries(question, rewritten)
    # Un placeholder de entorno (__SET_ME__) NUNCA debe filtrar vectores: dejaría
    # la rama vectorial vacía y el turno caería a FTS o a insuficiencia.
    embedding_model = s.EMBEDDING_MODEL if s.EMBEDDING_MODEL not in PLACEHOLDERS else None
    embedding_version = s.PIPELINE_VERSION if s.PIPELINE_VERSION not in PLACEHOLDERS else None

    return RetrievalPlan(
        question=question,
        queries=queries,
        candidate_k=candidate_k,
        final_k=final_k,
        doc_quota=float(s.RETRIEVAL_DOC_QUOTA),
        max_per_file=int(s.RETRIEVAL_MAX_PER_FILE),
        max_per_page=int(s.RETRIEVAL_MAX_PER_PAGE),
        enable_rerank=True,
        expand_neighbors=intent in (Intent.LOOKUP, Intent.ANALYSIS, Intent.READ),
        use_graph=intent in (Intent.LOOKUP, Intent.ANALYSIS),
        embedding_model=embedding_model,
        embedding_version=embedding_version,
        strict_embedding_version=bool(s.RETRIEVAL_STRICT_EMBEDDING_VERSION),
    )


def replace_queries(plan: RetrievalPlan, queries: list[str]) -> RetrievalPlan:
    """Devuelve una copia del plan con las consultas expandidas por el grafo."""
    from dataclasses import replace

    return replace(plan, queries=tuple(queries[:MAX_QUERIES]))


def as_dict(plan: RetrievalPlan) -> dict[str, Any]:
    """Representación estable para claves de caché y logs."""
    return {
        "question": plan.question,
        "queries": list(plan.queries),
        "candidate_k": plan.candidate_k,
        "final_k": plan.final_k,
        "doc_quota": plan.doc_quota,
        "max_per_file": plan.max_per_file,
        "max_per_page": plan.max_per_page,
        "use_graph": plan.use_graph,
        "embedding_model": plan.embedding_model,
        "embedding_version": plan.embedding_version,
        "strict_embedding_version": plan.strict_embedding_version,
    }


__all__ = ["RetrievalPlan", "build", "replace_queries", "as_dict", "MAX_QUERIES"]
