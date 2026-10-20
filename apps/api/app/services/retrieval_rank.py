"""Reranker determinista + dedupe + diversidad (contrato §1.4).

Entra la lista fusionada por RRF y sale la lista que verá la síntesis:

1. dedupe de la misma página entre modos OCR (`basico` vs `document_ai`),
2. dedupe/fusión de segmentos contiguos del mismo hablante,
3. score determinista (cobertura, entidad, prior de fuente, proximidad),
4. cuotas por archivo/página y MMR (lambda 0.7),
5. vecinos ±1 solo como contexto (nunca citas nuevas).

No consulta la base de datos ni llama a un modelo: mismas entradas, mismo
orden. El cross-encoder opcional vive en `retrieval_crossencoder` y solo
reordena el conjunto ya diversificado.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable

from app.services import text_terms
from app.services.intent import Intent
from app.services.retrieval_plan import RetrievalPlan

WEIGHT_COVERAGE = 0.40
WEIGHT_ENTITY = 0.25
WEIGHT_SOURCE = 0.20
WEIGHT_PROXIMITY = 0.15
MMR_LAMBDA = 0.7
NEIGHBOR_WINDOW_MS = 2000

_PROPER_NOUN = re.compile(
    r"\b[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}(?:\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,})*\b"
)

_TESTIMONY_TERMS = frozenset({
    "testigo", "testigos", "testimonio", "testimonios", "declaracion", "declaraciones",
    "declaro", "declararon", "audiencia", "audiencias", "hablante", "hablantes",
    "intervencion", "intervenciones", "intervino", "intervinieron", "video", "videos",
    "audio", "audios", "grabacion", "grabaciones", "transcripcion", "transcripciones",
    "minuto", "minutos", "dijo", "dijeron", "hablo", "hablaron",
})


# --- utilidades ---------------------------------------------------------------

def _meta(item: dict) -> dict:
    meta = item.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001 — metadata sucia no rompe el ranking
            meta = {}
    return meta if isinstance(meta, dict) else {}


def _score_value(item: dict) -> float:
    try:
        return float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_document(item: dict) -> bool:
    return bool(item.get("document_id")) or item.get("source_type") == "document_page"


def _haystack(item: dict) -> str:
    parts = [item.get("text"), item.get("filename"), item.get("speaker"), item.get("name")]
    return " ".join(str(p) for p in parts if p)


def _item_terms(item: dict) -> set[str]:
    return text_terms.content_set(_haystack(item))


def _text_terms(item: dict) -> set[str]:
    """Términos del TEXTO: la similitud MMR no debe contar el nombre del archivo."""
    return text_terms.content_set(str(item.get("text") or ""))


def _stable_key(item: dict) -> tuple:
    ident = item.get("id")
    return (
        str(item.get("filename") or ""),
        int(item.get("page_number") or 0),
        int(item.get("start_ms") or 0),
        str(ident if ident is not None else item.get("segment_id") or ""),
    )


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _is_testimony_question(question: str, intent: Intent) -> bool:
    if intent is Intent.NAVIGATE:
        return True
    return bool(text_terms.content_set(question) & _TESTIMONY_TERMS)


def _proper_nouns(question: str) -> list[str]:
    """Nombres propios escritos con mayúscula, excluyendo la primera palabra."""
    stripped = (question or "").lstrip("¿¡ \t")
    out: list[str] = []
    for match in _PROPER_NOUN.finditer(stripped):
        if match.start() == 0:
            continue  # «Pagos…» al inicio de la frase no es un nombre propio
        folded = text_terms.norm(match.group(0))
        if folded in text_terms.STOPWORDS or folded in text_terms.QUESTION_WORDS:
            continue
        if len(folded) < 3:
            continue
        out.append(folded)
    return out


def _term_coverage(question_terms: set[str], haystack_terms: set[str]) -> float:
    """Cobertura con tolerancia morfológica mínima (pago/pagos, demanda/demandas).

    `text_terms.coverage` sigue siendo la definición compartida para claims; aquí
    el ranking necesita reconocer la flexión para no castigar a cero un documento
    que habla de «pago» cuando la pregunta dice «pagos».
    """
    if not question_terms:
        return 0.5
    hits = 0
    for term in question_terms:
        if term in haystack_terms:
            hits += 1
            continue
        if len(term) >= 4 and any(
            len(other) >= 4 and abs(len(other) - len(term)) <= 2
            and (other.startswith(term) or term.startswith(other))
            for other in haystack_terms
        ):
            hits += 1
    return hits / len(question_terms)


def _entities(item: dict) -> list[str]:
    meta = _meta(item)
    raw = meta.get("entities")
    values: list[str] = []
    if isinstance(raw, list):
        for value in raw:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, dict):
                for key in ("name", "label", "text", "value"):
                    if isinstance(value.get(key), str):
                        values.append(value[key])
                        break
    elif isinstance(raw, str):
        values.extend(part for part in raw.split(",") if part.strip())
    return [text_terms.norm(v).strip() for v in values if v.strip()]


def _entity_score(question: str, item: dict, question_terms: set[str]) -> float:
    haystack = text_terms.norm(_haystack(item))
    names = _proper_nouns(question)
    if names and any(name in haystack for name in names):
        return 1.0
    entities = _entities(item)
    if entities and any(entity and entity in question_terms for entity in entities):
        return 0.6
    if entities:
        for entity in entities:
            if entity and any(term in entity for term in question_terms if len(term) >= 4):
                return 0.4
    return 0.0


# --- dedupe -------------------------------------------------------------------

def _better_document(candidate: dict, current: dict) -> bool:
    candidate_score = _score_value(candidate)
    current_score = _score_value(current)
    if abs(candidate_score - current_score) > 1e-9:
        return candidate_score > current_score
    # Empate: el OCR avanzado suele traer mejor texto.
    return (_meta(candidate).get("ocr_mode") == "document_ai"
            and _meta(current).get("ocr_mode") != "document_ai")


def _dedupe_documents(items: Iterable[dict]) -> list[dict]:
    kept: dict[tuple, dict] = {}
    for item in items:
        key = (str(item.get("document_id")), item.get("page_number"))
        current = kept.get(key)
        if current is None or _better_document(item, current):
            kept[key] = item
    return list(kept.values())


def _dedupe_transcripts(items: Iterable[dict]) -> list[dict]:
    # (1) mismo hablante en la misma ventana de 2 s.
    by_window: dict[tuple, dict] = {}
    for item in items:
        media = str(item.get("media_id") or "")
        speaker = text_terms.norm(item.get("speaker") or "")
        start = int(item.get("start_ms") or 0)
        key = (media, speaker, start // NEIGHBOR_WINDOW_MS)
        current = by_window.get(key)
        if current is None or _score_value(item) > _score_value(current):
            by_window[key] = item

    # (2) segmentos contiguos del mismo hablante (gap <= 2 s): se fusionan.
    ordered = sorted(
        by_window.values(),
        key=lambda it: (str(it.get("media_id") or ""), text_terms.norm(it.get("speaker") or ""),
                        int(it.get("start_ms") or 0)),
    )
    merged: list[dict] = []
    for item in ordered:
        if not merged:
            merged.append(dict(item))
            continue
        prev = merged[-1]
        same_source = (str(prev.get("media_id")) == str(item.get("media_id"))
                       and text_terms.norm(prev.get("speaker") or "")
                       == text_terms.norm(item.get("speaker") or ""))
        if same_source and _is_contiguous(prev, item):
            _merge_segment(prev, item)
            continue
        merged.append(dict(item))
    return merged


def _is_contiguous(previous: dict, current: dict) -> bool:
    start = int(current.get("start_ms") or 0)
    end = previous.get("end_ms")
    if end is None:
        base = int(previous.get("start_ms") or 0)
    else:
        base = int(end)
    return 0 <= start - base <= NEIGHBOR_WINDOW_MS


def _merge_segment(previous: dict, current: dict) -> None:
    """Conserva el minuto inicial y el texto más largo; el resto es contexto."""
    previous_text = str(previous.get("text") or "")
    current_text = str(current.get("text") or "")
    if len(current_text) > len(previous_text):
        previous["text"] = current_text
    current_end = current.get("end_ms")
    previous_end = previous.get("end_ms")
    previous["end_ms"] = max(int(previous_end or 0), int(current_end or 0)) or previous_end
    if _score_value(current) > _score_value(previous):
        previous["score"] = current.get("score")
        if current.get("segment_id"):
            previous["segment_id"] = current.get("segment_id")


def dedupe(items: list[dict]) -> list[dict]:
    """Dedupe OCR + testimonio, preservando el orden de entrada razonable."""
    documents = _dedupe_documents([it for it in items if _is_document(it)])
    transcripts = _dedupe_transcripts([it for it in items if not _is_document(it)])
    return documents + transcripts


# --- score --------------------------------------------------------------------

def score_item(question: str, item: dict, *, intent: Intent,
               max_raw_score: float, coverage: float | None = None) -> float:
    """Score determinista 0..1 de un candidato."""
    question_terms = text_terms.content_set(question)
    coverage_score = _term_coverage(question_terms, _item_terms(item))
    entity = _entity_score(question, item, question_terms)
    testimony = _is_testimony_question(question, intent)
    document = _is_document(item)
    if document:
        prior = 0.7 if testimony else 1.0
    else:
        prior = 1.0 if testimony else 0.7
    raw = _score_value(item)
    proximity = (raw / max_raw_score) if max_raw_score > 0 else 0.0
    proximity = min(1.0, max(0.0, proximity))

    if coverage is not None and coverage < 0.5:
        # Pregunta poco cubierta por el expediente: pesa más el tema y menos la
        # posición en la lista fusionada. No se descarta nada aquí.
        total = 0.55 * coverage_score + 0.20 * entity + 0.15 * prior + 0.10 * proximity
    else:
        total = (WEIGHT_COVERAGE * coverage_score + WEIGHT_ENTITY * entity
                 + WEIGHT_SOURCE * prior + WEIGHT_PROXIMITY * proximity)
    return round(min(1.0, max(0.0, total)), 6)


# --- diversidad ---------------------------------------------------------------

def _file_key(item: dict) -> str:
    name = item.get("filename")
    if name:
        return str(name)
    if item.get("document_id"):
        return f"doc:{item['document_id']}"
    return f"media:{item.get('media_id')}"


def _page_key(item: dict) -> str:
    if _is_document(item):
        if item.get("page_number") is None:
            return f"chunk:{item.get('id')}"
        return f"page:{item.get('document_id')}:{item.get('page_number')}"
    if item.get("start_ms") is None:
        return f"seg:{item.get('segment_id') or item.get('id')}"
    return f"min:{item.get('media_id')}:{int(item.get('start_ms')) // 60000}"


def _pick_mmr(pool: list[dict], selected: list[dict], terms: dict[int, set[str]],
              *, lam: float = MMR_LAMBDA) -> dict | None:
    selected_terms = [terms[id(it)] for it in selected]
    best: dict | None = None
    best_value = float("-inf")
    for item in pool:
        relevance = float(item.get("rank_score") or 0.0)
        similarity = max((_jaccard(terms[id(item)], other) for other in selected_terms), default=0.0)
        value = lam * relevance - (1.0 - lam) * similarity
        if value > best_value + 1e-12:
            best, best_value = item, value
        elif abs(value - best_value) <= 1e-12 and best is not None:
            if _stable_key(item) < _stable_key(best):
                best = item
    return best


def _select(items: list[dict], plan: RetrievalPlan, *, reserve_docs: bool = True) -> list[dict]:
    if not items:
        return []
    terms = {id(it): _text_terms(it) for it in items}
    pool = sorted(items, key=lambda it: (-float(it.get("rank_score") or 0.0), _stable_key(it)))
    selected: list[dict] = []
    file_counts: dict[str, int] = {}
    page_counts: dict[str, int] = {}

    def can_take(item: dict) -> bool:
        return (file_counts.get(_file_key(item), 0) < plan.max_per_file
                and page_counts.get(_page_key(item), 0) < plan.max_per_page)

    def take(item: dict) -> None:
        selected.append(item)
        for i, candidate in enumerate(pool):
            if candidate is item:
                del pool[i]
                break
        file_counts[_file_key(item)] = file_counts.get(_file_key(item), 0) + 1
        page_counts[_page_key(item)] = page_counts.get(_page_key(item), 0) + 1

    # Cuota de documentos: reserva de slots, no un techo para las transcripciones.
    # En preguntas de testimonio no se reserva nada: el audio es la fuente primaria.
    doc_slots = int(plan.final_k * plan.doc_quota) if reserve_docs else 0
    while len(selected) < min(doc_slots, plan.final_k):
        documents = [it for it in pool if _is_document(it) and can_take(it)]
        if not documents:
            break
        pick = _pick_mmr(documents, selected, terms)
        if pick is None:
            break
        take(pick)

    while len(selected) < plan.final_k:
        candidates = [it for it in pool if can_take(it)]
        if not candidates:
            break
        pick = _pick_mmr(candidates, selected, terms)
        if pick is None:
            break
        take(pick)

    return selected[:plan.final_k]


# --- API ----------------------------------------------------------------------

def apply(question: str, items: list[dict], *, plan: RetrievalPlan,
          intent: Intent, coverage: float | None = None) -> list[dict]:
    """Ordena y diversifica los candidatos. Devuelve `list[dict]` sin handles.

    `coverage` es la cobertura léxica global de la pregunta en el expediente
    (`abstention.term_coverage`). Si es baja, el ranking prioriza el tema sobre
    la posición y no descarta candidatos: la abstención sigue siendo una señal,
    no una puerta.
    """
    if not items:
        return []

    deduped = dedupe(items)
    max_raw = max((_score_value(it) for it in deduped), default=0.0)

    scored: list[dict] = []
    for item in deduped:
        candidate = dict(item)
        candidate["rank_score"] = score_item(question, candidate, intent=intent,
                                            max_raw_score=max_raw, coverage=coverage)
        candidate["rerank_score"] = candidate["rank_score"]
        scored.append(candidate)

    selected = _select(scored, plan,
                       reserve_docs=not _is_testimony_question(question, intent))

    out: list[dict] = []
    for item in selected:
        cleaned = dict(item)
        cleaned.pop("handle", None)
        if plan.expand_neighbors:
            neighbors = _meta(cleaned).get("neighbors")
            if isinstance(neighbors, list) and neighbors:
                # Solo contexto ya presente en el item; no son citas nuevas.
                cleaned["neighbors"] = neighbors[:2]
        out.append(cleaned)
    return out


__all__ = ["apply", "dedupe", "score_item", "MMR_LAMBDA"]
