"""F4 · R3 y R6.2 — publicación sin amputación y control del Markdown narrativo.

T4/T6 del contrato: un `UNSUPPORTED` con cita primaria válida se publica con
`needs_review=True`; uno sin cita válida no sale. Si el gate bloqueó algo, el
informe narrativo del modelo se descarta y la respuesta lo dice.
"""
from __future__ import annotations

import pytest

from app.routers import query
from app.services import compose
from app.services.intent import Intent

pytestmark = pytest.mark.unit

EVIDENCE = [
    {"handle": "E1", "source_type": "transcript_segment", "media_id": "m1", "segment_id": "s1",
     "start_ms": 1000, "end_ms": 5000, "filename": "0065GrabacionAudiencia13Dic2024.mp4",
     "speaker": "JORGE H. ROJAS M.", "text": "El pago se hizo el 13 de diciembre de 2024."},
]
QUESTION = "¿Qué dijo Jorge Rojas sobre el pago?"
CLAIM = {"text": "Jorge Humberto Rojas Melo afirmó que el pago se hizo.", "citations": ["E1"]}
CLAIM_LEAD = "Jorge Humberto Rojas Melo afirmó que el pago se hizo"
MARKDOWN = (
    "## Síntesis\n\n"
    "Jorge Humberto Rojas Melo afirmó que el pago se hizo el 13 de diciembre de 2024 [E1]. "
    "La afirmación consta en la audiencia y no se contrasta con otros medios. "
    "El expediente no contiene el comprobante de ese pago.\n"
)


def _claim(*, status: str, code: str | None = None, citations: list[str] | None = None,
           text: str | None = None) -> dict:
    reasons = [{"code": code}] if code else []
    return {"claim_index": 0, "text": text or CLAIM["text"],
            "citations": CLAIM["citations"] if citations is None else citations,
            "status": status, "reasons": reasons}


def _guard(*claims: dict) -> dict:
    return {
        "status": "BLOCKED",
        "claims": list(claims),
        "approved_claims": [
            {"text": claim["text"], "citations": claim["citations"]}
            for claim in claims if claim.get("status") == "SUPPORTED"
        ],
        "findings": [],
        "limits": [],
    }


# --- A8/A9: qué se publica ---------------------------------------------------------

def test_a8_unsupported_with_valid_citation_is_published_for_review():
    guard = _guard(_claim(status="UNSUPPORTED", code="DATE_MISMATCH"))

    published = query._publication_claims(guard)
    notes = query._publication_notes(guard, published)

    assert len(published) == 1
    assert published[0]["text"] == CLAIM["text"]
    assert published[0]["needs_review"] is True
    assert published[0]["review_reason"] == "DATE_MISMATCH"
    assert notes == ["1 afirmación requiere revisión humana (motivo: DATE_MISMATCH)."]


def test_a9_unresolved_citation_is_not_published_and_is_counted():
    guard = _guard(_claim(status="UNSUPPORTED", code="CITATION_UNRESOLVED"))

    published = query._publication_claims(guard)
    notes = query._publication_notes(guard, published)

    assert published == []
    assert notes == ["Se omitió 1 afirmación sin cita primaria válida."]


def test_missing_citation_is_not_published():
    guard = _guard(_claim(status="UNSUPPORTED", code="CITATION_MISSING", citations=[]))

    assert query._publication_claims(guard) == []


@pytest.mark.parametrize("code", ["QUOTE_MISMATCH", "QUOTE_HASH_UNVERIFIED"])
def test_quote_problems_are_not_published(code):
    guard = _guard(_claim(status="UNSUPPORTED", code=code))

    assert query._publication_claims(guard) == []


def test_number_mismatch_is_published_as_review_never_as_approved():
    """R5: una cifra ausente no se convierte en aprobada con la nueva publicación."""
    guard = _guard(_claim(status="UNSUPPORTED", code="NUMBER_MISMATCH"))

    published = query._publication_claims(guard)

    assert len(published) == 1
    assert published[0].get("needs_review") is True
    assert published[0]["review_reason"] == "NUMBER_MISMATCH"


def test_needs_review_claim_is_published_with_its_reason():
    guard = _guard(_claim(status="NEEDS_REVIEW", code="PARTY_MISMATCH"))

    published = query._publication_claims(guard)

    assert len(published) == 1
    assert published[0]["needs_review"] is True
    assert published[0]["review_reason"] == "PARTY_MISMATCH"


def test_approved_claims_are_published_without_review_flag():
    guard = _guard(_claim(status="SUPPORTED"))

    published = query._publication_claims(guard)

    assert published == [{"text": CLAIM["text"], "citations": ["E1"]}]


def test_old_fixed_sentence_is_gone_and_counts_are_honest():
    guard = _guard(
        _claim(status="UNSUPPORTED", code="DATE_MISMATCH"),
        _claim(status="UNSUPPORTED", code="CITATION_UNRESOLVED", text="Otra afirmación."),
    )

    published = query._publication_claims(guard)
    notes = query._publication_notes(guard, published)

    assert all("sin soporte legal verificable" not in note for note in notes)
    assert notes == [
        "1 afirmación requiere revisión humana (motivo: DATE_MISMATCH).",
        "Se omitió 1 afirmación sin cita primaria válida.",
    ]


def test_plural_counts():
    guard = _guard(
        _claim(status="UNSUPPORTED", code="DATE_MISMATCH"),
        _claim(status="UNSUPPORTED", code="PARTY_MISMATCH", text="Segunda afirmación."),
        _claim(status="UNSUPPORTED", code="CITATION_UNRESOLVED", text="Tercera afirmación."),
        _claim(status="UNSUPPORTED", code="CITATION_MISSING", citations=[], text="Cuarta."),
    )

    published = query._publication_claims(guard)
    notes = query._publication_notes(guard, published)

    assert notes == [
        "2 afirmaciones requieren revisión humana (motivo: DATE_MISMATCH).",
        "Se omitieron 2 afirmaciones sin cita primaria válida.",
    ]


# --- R6.2: el Markdown no puede contradecir al gate --------------------------------

def test_r6_2_blocked_claim_keeps_the_markdown_and_flags_review():
    guard = _guard(_claim(status="UNSUPPORTED", code="NUMBER_MISMATCH"))
    validated = {"claims": [CLAIM], "uncertainties": [], "answer_markdown": MARKDOWN}

    notes = query._apply_publication(validated, guard)

    assert validated["answer_markdown"] == MARKDOWN
    assert query.GUARD_REPLACED_NARRATIVE in notes
    assert len([note for note in notes if note == query.GUARD_REPLACED_NARRATIVE]) == 1

    composed = compose.render(
        QUESTION, intent=Intent.LOOKUP, claims=validated["claims"], evidence=EVIDENCE,
        uncertainties=notes, locale="es", answer_markdown=validated["answer_markdown"])

    assert "## Síntesis" in composed.answer
    assert CLAIM_LEAD in composed.answer
    assert query.GUARD_REPLACED_NARRATIVE in composed.answer


def test_r6_2_without_blocks_the_markdown_is_kept():
    guard = _guard(_claim(status="SUPPORTED"))
    validated = {"claims": [CLAIM], "uncertainties": [], "answer_markdown": MARKDOWN}

    notes = query._apply_publication(validated, guard)

    assert validated["answer_markdown"] == MARKDOWN
    assert query.GUARD_REPLACED_NARRATIVE not in notes


# --- R3.4: el compositor muestra los claims en revisión ----------------------------

def test_review_claim_is_shown_once_with_the_aggregate_limit():
    claims = [dict(CLAIM, needs_review=True, review_reason="DATE_MISMATCH")]
    note = query._review_note(1, "DATE_MISMATCH")

    composed = compose.render(
        QUESTION, intent=Intent.LOOKUP, claims=claims, evidence=EVIDENCE,
        uncertainties=[note, note], locale="es")

    assert composed.answer.count(CLAIM_LEAD) == 1
    assert composed.answer.count(note) == 1
