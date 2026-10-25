"""Compositor determinista de la respuesta del chat (contrato §1.6).

El LLM propone *claims* con handles; este módulo decide qué se muestra, en qué
orden, con cuántas citas y bajo qué tipo de respuesta. No llama al modelo y no
depende del proveedor: la misma evidencia produce el mismo Markdown.

Reglas duras:
- una afirmación sin cita válida no se muestra;
- una cifra/fecha que no aparece en la evidencia citada se mueve a «Límites»;
- un fragmento copiado del ASR (>85 % literal y <20 % de solape con la pregunta)
  se degrada a «Lo que dice el audio», nunca a respuesta directa;
- presupuesto de citas: ≤ MAX_VISIBLE_CITATIONS, sin duplicar página ni segmento,
  y solo se listan las fuentes referenciadas. Una afirmación se muestra con TODAS
  sus fuentes; si el presupuesto global no alcanza, se omite la afirmación.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.core.config import get_settings
from app.core.i18n import t
from app.services import evidence_meta, text_terms

DIRECT = "direct"
PARTIAL = "partial"
INSUFFICIENT = "insufficient"
NAVIGATIONAL = "navigational"
CORRECTION = "correction"

_ASR_COPY_THRESHOLD = 0.85
_ASR_OVERLAP_MAX = 0.20
_QUESTION_OVERLAP_MIN = 0.34
_CONVERSATIONAL = (
    "perdon", "perdón", "mire", "bueno", "eh", "o sea", "este ", "esto es",
    "digamos", "verdad", "entonces",
)
_NAME_RE = re.compile(r"\b[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}\b")
_DATE_RE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b(?:enero|febrero|marzo|abril|mayo|junio|julio|"
    r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b|\b(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_WHEN_RE = re.compile(r"\b(cuando|fecha|fechas|dia|dias|mes|meses|ano|anos|plazo)\b")
_WHO_RE = re.compile(r"\b(quien|quienes|nombre|nombres|firmo|firmaron|suscribio)\b")
_HOW_MUCH_RE = re.compile(r"\b(cuanto|cuanta|cuantos|cuantas|monto|valor|suma|cuantia|total)\b")
_MONEY_RE = re.compile(r"[$\u20ac]\s*\d|\b\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?\b")


@dataclass
class ComposeResult:
    answer_kind: str
    answer: str
    used_handles: list[str]
    sections: dict[str, list[str]] = field(default_factory=dict)
    uncertainties: list[str] = field(default_factory=list)


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _norm(text: str | None) -> str:
    return text_terms.norm(text)


def _context(item: dict) -> str:
    """Texto citable + metadatos visibles (página, minuto, hablante, archivo).

    Los números de la respuesta pueden venir de la etiqueta de la fuente
    («minuto 01:26») y no del cuerpo del texto; el validador debe verlos.
    F4 · R2: una sola definición compartida con `answering` y el gate legal.
    """
    return evidence_meta.context(item)


def _stem_match(left: str, right: str) -> bool:
    """Tolerancia morfológica conservadora: pago/pagos, demanda/demandas."""
    if left == right:
        return True
    if min(len(left), len(right)) < 4:
        return False
    return left[:4] == right[:4] or left.startswith(right) or right.startswith(left)


def _overlap(claim: str, question: str) -> float:
    """Solape con la pregunta, con tolerancia a plurales/singulares.

    `text_terms.overlap_with_question` es la referencia del contrato; se
    complementa con prefijos de 4 letras porque el pipeline no hace stemming.
    """
    base = text_terms.overlap_with_question(claim, question)
    if base >= _QUESTION_OVERLAP_MIN:
        return base
    q = text_terms.content_set(question)
    c = text_terms.content_set(claim)
    if not q:
        return base
    matched = sum(1 for term in q if any(_stem_match(term, other) for other in c))
    return max(base, matched / len(q))


def _fills_slot(question: str, claim: str, intent: Any) -> bool:
    """Una ranura de intención llena (quién/cuándo/cuánto) también responde."""
    q = _norm(question)
    if _WHO_RE.search(q) and _NAME_RE.search(claim):
        return True
    if _WHEN_RE.search(q) and _DATE_RE.search(claim):
        return True
    if _HOW_MUCH_RE.search(q) and _MONEY_RE.search(claim):
        return True
    intent_value = str(getattr(intent, "value", intent) or "")
    if intent_value == "agregacion" and text_terms.numbers(claim):
        return True
    return False


def _responds(question: str, claim: str, intent: Any) -> bool:
    return _overlap(claim, question) >= _QUESTION_OVERLAP_MIN or _fills_slot(question, claim, intent)


def _is_asr_literal(claim: str, cited_text: str, question: str, source_types: set[str]) -> bool:
    if not source_types or not source_types <= {"transcript_segment"}:
        return False
    if not cited_text:
        return False
    if text_terms.coverage(claim, cited_text) <= _ASR_COPY_THRESHOLD:
        return False
    return text_terms.overlap_with_question(claim, question) < _ASR_OVERLAP_MAX


def _is_conversational(claim: str, question: str) -> bool:
    folded = _norm(claim).lstrip("«\"'¡¿ ")
    if not any(folded.startswith(prefix) for prefix in _CONVERSATIONAL):
        return False
    if len(claim) >= 220:
        return False
    return text_terms.overlap_with_question(claim, question) == 0


def _location_key(item: dict) -> tuple:
    if item.get("source_type") == "document_page" or item.get("document_id"):
        return ("doc", str(item.get("document_id")), item.get("page_number"))
    return ("media", str(item.get("media_id")), str(item.get("segment_id")))


def _is_citable(item: dict) -> bool:
    """`citations` solo admite documento+página o media+segmento (CHECK del schema)."""
    if item.get("document_id") and item.get("page_number") is not None:
        return True
    if item.get("media_id") and item.get("segment_id") and item.get("start_ms") is not None:
        try:
            return int(item.get("end_ms") or 0) > int(item.get("start_ms") or 0)
        except (TypeError, ValueError):
            return False
    return False


def _file_key(item: dict) -> str:
    return str(item.get("filename") or item.get("document_id") or item.get("media_id") or "?")


def _dedupe_texts(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean(value)
        key = _norm(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def _with_markers(text: str, handles: list[str], markers: dict[str, int]) -> str:
    clean = _clean(text)
    if not clean:
        return clean
    suffix = "".join(f" [{markers[h]}]" for h in handles if h in markers)
    if not suffix:
        return clean
    if clean[-1] in ".!?;:":
        return f"{clean[:-1]}{suffix}{clean[-1]}"
    return f"{clean}{suffix}."


def _source_line(item: dict, marker: int, locale: str) -> str:
    if item.get("source_type") == "document_page" or item.get("document_id"):
        filename = item.get("filename") or t("messages.compose_unnamed_source", locale)
        page = item.get("page_number")
        folio = f" (folio {item['folio']})" if item.get("folio") not in (None, "") and str(item.get("folio")) != str(page) else ""
        page_txt = f" · p. {page}" if page not in (None, "") else ""
        return f"[{marker}] {filename}{page_txt}{folio}"
    filename = item.get("filename") or item.get("media_id") or "?"
    minute = item.get("start_mmss") or text_terms.mmss(item.get("start_ms"))
    speaker = item.get("speaker") or ""
    tail = " · ".join(part for part in (minute, speaker) if part)
    return f"[{marker}] {filename}" + (f" · {tail}" if tail else "")


# F3 · R4: informe narrativo del modelo. Los handles se renumeran en orden de
# primera aparición y el Markdown se conserva íntegro (títulos, listas, tablas).
_CITE_RE = re.compile(r"\[([A-Z]{1,3}\d+(?:\s*,\s*[A-Z]{1,3}\d+)*)\]")
_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.M)


def _marker_handles(match: re.Match) -> list[str]:
    return [part.strip() for part in match.group(1).split(",")]


def _narrative_markdown(markdown: str | None, by_handle: dict[str, dict],
                        max_citations: int) -> tuple[str, list[str], dict[str, int]] | None:
    """Markdown renumerado + handles usados, o None para usar el camino legado.

    Se descarta el informe completo (no se recorta) si cita un handle que no
    resuelve o si necesita más citas que el presupuesto: una tabla o una frase
    con la mitad de sus fuentes es peor que el detalle verificado.
    """
    if not isinstance(markdown, str):
        return None
    text = markdown.strip()
    if not text or not _HEADING_RE.search(text):
        return None
    used: list[str] = []
    for match in _CITE_RE.finditer(text):
        for handle in _marker_handles(match):
            item = by_handle.get(handle)
            if item is None or not _is_citable(item):
                return None
            if handle not in used:
                used.append(handle)
    if not used or len(used) > max_citations:
        return None
    markers = {handle: index for index, handle in enumerate(used, 1)}

    def _replace(match: re.Match) -> str:
        return "[" + ", ".join(str(markers[h]) for h in _marker_handles(match)) + "]"

    return _CITE_RE.sub(_replace, text), used, markers


def render(question: str, *, intent: Any, claims: list[dict], evidence: list[dict],
           uncertainties: list[str], locale: str, max_citations: int | None = None,
           max_per_file: int | None = None, coverage: float | None = None,
           deep: bool = False, answer_markdown: str | None = None) -> ComposeResult:
    """Compone el Markdown final a partir de claims validados y evidencia."""
    settings = get_settings()
    max_citations = max_citations or settings.MAX_VISIBLE_CITATIONS
    # `max_per_file` se conserva por compatibilidad de la firma; ya no se usa
    # como recorte porque podía dejar una afirmación con una cita incompleta.
    limits = _dedupe_texts(uncertainties)
    intent_value = str(getattr(intent, "value", intent) or "")

    if intent_value == "navegar":
        return ComposeResult(
            answer_kind=NAVIGATIONAL,
            answer=t("messages.compose_navigational", locale),
            used_handles=[],
            sections={"direct": [], "evidence": [], "limits": [], "sources": []},
            uncertainties=limits,
        )

    by_handle = {str(it.get("handle")): it for it in evidence if it.get("handle")}

    narrative = _narrative_markdown(answer_markdown, by_handle, max_citations)
    if narrative is not None:
        body, used, markers = narrative
        sources = [_source_line(by_handle[h], markers[h], locale) for h in used]
        claim_texts = [_clean(c.get("text")) for c in claims or [] if isinstance(c, dict)]
        claim_texts = [text for text in claim_texts if text]
        if claim_texts:
            # En análisis exhaustivo el informe se publica completo aunque ninguna
            # afirmación aislada «responda» a la pregunta.
            responds = any(_responds(question, text, intent) for text in claim_texts)
            answer_kind = DIRECT if (responds or deep) else PARTIAL
        else:
            # `INSUFFICIENT` exige ausencia de claims Y de markdown válido.
            answer_kind = DIRECT
        blocks = [body]
        if limits:
            blocks.append("## " + t("messages.compose_limits", locale) + "\n"
                          + "\n".join(f"- {value}" for value in limits))
        if sources:
            blocks.append("## " + t("messages.compose_sources", locale) + "\n" + "\n".join(sources))
        return ComposeResult(
            answer_kind=answer_kind,
            answer="\n\n".join(blocks),
            used_handles=used,
            sections={"direct": [body], "evidence": [], "limits": limits, "sources": sources},
            uncertainties=limits,
        )

    prepared: list[dict] = []
    seen_texts: set[str] = set()

    for raw_claim in claims or []:
        if not isinstance(raw_claim, dict):
            continue
        text = _clean(raw_claim.get("text"))
        if not text:
            continue
        key = _norm(text)
        if key in seen_texts:
            continue
        handles: list[str] = []
        for handle in raw_claim.get("citations") or []:
            handle = str(handle)
            if (handle in by_handle and handle not in handles
                    and _is_citable(by_handle[handle])):
                handles.append(handle)
        if not handles:
            continue
        cited_text = " ".join(_context(by_handle[h]) for h in handles)
        if text_terms.missing_numbers(text, cited_text):
            limits.append(t("messages.compose_unverified_number", locale).format(text=text))
            continue
        seen_texts.add(key)
        source_types = {str(by_handle[h].get("source_type") or "") for h in handles}
        if (_is_asr_literal(text, cited_text, question, source_types)
                or ("transcript_segment" in source_types and _is_conversational(text, question))):
            prepared.append({"text": text, "citations": handles, "kind": "audio"})
            continue
        kind = "direct" if _responds(question, text, intent) else "evidence"
        prepared.append({"text": text, "citations": handles, "kind": kind})

    used: list[str] = []
    markers: dict[str, int] = {}
    seen_locations: set[tuple] = set()

    def _allocate(candidate: dict) -> list[str]:
        allocated: list[str] = []
        for handle in candidate["citations"]:
            if handle in markers:
                allocated.append(handle)
                continue
            if len(used) >= max_citations:
                continue
            item = by_handle[handle]
            location = _location_key(item)
            if location in seen_locations:
                continue
            used.append(handle)
            markers[handle] = len(used)
            seen_locations.add(location)
            allocated.append(handle)
        return allocated

    rendered: list[dict] = []
    # El texto final pinta primero la respuesta directa y luego el material de
    # apoyo: los marcadores [n] se asignan en ese mismo orden de referencia.
    order = {"direct": 0, "evidence": 1, "audio": 2}
    ordered = sorted(prepared, key=lambda c: order.get(c["kind"], 3))
    if not any(c["kind"] == "direct" for c in prepared):
        # `partial`: en modo normal solo los 2–3 fragmentos más cercanos; en
        # análisis exhaustivo se muestran todos los que quepan en el presupuesto.
        related_count = 0
        trimmed: list[dict] = []
        limit = 12 if deep else 3
        for candidate in ordered:
            if candidate["kind"] == "evidence":
                related_count += 1
                if related_count > limit:
                    continue
            trimmed.append(candidate)
        ordered = trimmed
    for candidate in ordered:
        before_used = list(used)
        before_markers = dict(markers)
        before_locations = set(seen_locations)
        allocated = _allocate(candidate)
        if len(allocated) < len(candidate["citations"]):
            # El presupuesto global no alcanzó para todas las fuentes de esta
            # afirmación: mejor omitirla que mostrarla con una cita parcial.
            used[:] = before_used
            markers.clear()
            markers.update(before_markers)
            seen_locations.clear()
            seen_locations.update(before_locations)
            continue
        if allocated:
            rendered.append({**candidate, "citations": allocated})

    direct = [c for c in rendered if c["kind"] == "direct"]
    related = [c for c in rendered if c["kind"] == "evidence"]
    audio = [c for c in rendered if c["kind"] == "audio"]

    if not direct and not related and not audio:
        return ComposeResult(
            answer_kind=INSUFFICIENT,
            answer=t("messages.insufficient_evidence", locale),
            used_handles=[],
            sections={"direct": [], "evidence": [], "limits": [], "sources": []},
            uncertainties=limits,
        )

    answer_kind = DIRECT if direct else PARTIAL
    if answer_kind == PARTIAL:
        # Señal de abstención: si la pregunta no está cubierta por el expediente
        # (cobertura léxica < umbral) o pide un dato numérico que no aparece en
        # NINGUNA evidencia recuperada, el material es solo “relacionado” y no
        # debe presentarse como respuesta parcial con citas.
        evidence_text = " ".join(_context(item) for item in evidence)
        question_numbers = set(text_terms.numbers(question))
        missing_question_numbers = sorted(question_numbers - set(text_terms.numbers(evidence_text)))
        low_coverage = coverage is not None and coverage < settings.COMPOSE_MIN_COVERAGE
        # En análisis exhaustivo, la cobertura léxica baja no autoriza a tirar el
        # análisis: hay material relacionado y el usuario quiere saber qué dice.
        if (low_coverage or missing_question_numbers) and not deep:
            return ComposeResult(
                answer_kind=INSUFFICIENT,
                answer=t("messages.insufficient_evidence", locale),
                used_handles=[],
                sections={"direct": [], "evidence": [], "limits": [], "sources": []},
                uncertainties=limits,
            )
    sections: dict[str, list[str]] = {"direct": [], "evidence": [], "limits": [], "sources": []}
    blocks: list[str] = []

    if direct:
        paragraph = " ".join(
            _with_markers(c["text"], c["citations"], markers) for c in direct
        )
        sections["direct"] = [paragraph]
        blocks.append(paragraph)
    elif not deep:
        # En análisis exhaustivo no se antepone el cartel: el usuario pidió el
        # análisis y ve directamente la evidencia, con answer_kind=partial.
        header = t("messages.compose_partial_header", locale)
        sections["direct"] = [header]
        blocks.append(header)
        related = related[:3]

    if related:
        bullets = [
            "- " + _with_markers(c["text"], c["citations"], markers) for c in related
        ]
        sections["evidence"] = [b[2:] for b in bullets]
        blocks.append(f"**{t('messages.compose_evidence', locale)}**\n" + "\n".join(bullets))

    if audio:
        quotes = [
            "- «" + _clean(c["text"]).strip("«»") + "» " +
            "".join(f"[{markers[h]}]" for h in c["citations"])
            for c in audio
        ]
        sections["evidence"].extend([_clean(c["text"]) for c in audio])
        blocks.append(f"**{t('messages.compose_audio', locale)}**\n" + "\n".join(quotes))

    if limits:
        sections["limits"] = limits
        blocks.append(f"**{t('messages.compose_limits', locale)}**\n"
                      + "\n".join(f"- {value}" for value in limits))

    if used:
        sources = [_source_line(by_handle[h], markers[h], locale) for h in used]
        sections["sources"] = sources
        blocks.append(f"**{t('messages.compose_sources', locale)}**\n" + "\n".join(sources))

    return ComposeResult(
        answer_kind=answer_kind,
        answer="\n\n".join(blocks),
        used_handles=used,
        sections=sections,
        uncertainties=limits,
    )
