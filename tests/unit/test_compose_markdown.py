"""UT-CMP-MD — compositor narrativo: conserva el Markdown validado del modelo.

Contrato F3 · R4:
- un markdown válido se conserva íntegro (títulos, listas, tablas, negritas);
- `[E1]`/`[E1, E2]` se renumeran a `[1]`/`[1, 2]` en orden de primera aparición;
- un handle inexistente, más handles que el presupuesto o la falta de encabezados
  descartan el markdown y devuelven el camino legado;
- deep sin claims directos nunca antepone el cartel «Encontré material relacionado».
"""
from __future__ import annotations

import json

import pytest

from app.services import answering, compose
from app.services.intent import Intent

pytestmark = pytest.mark.unit

QUESTION = "¿Qué evidencia hay sobre el pago?"


def _doc(handle: str, page: int, text: str, filename: str = "demanda.pdf") -> dict:
    return {"handle": handle, "source_type": "document_page", "document_id": f"d-{page}",
            "page_number": page, "filename": filename, "text": text}


def _seg(handle: str, text: str, start_ms: int = 2_228_000, speaker: str = "Rojas Melo") -> dict:
    return {"handle": handle, "source_type": "transcript_segment", "media_id": "m1",
            "segment_id": f"s-{handle}", "start_ms": start_ms, "end_ms": start_ms + 4000,
            "speaker": speaker, "filename": "audiencia.mp4", "text": text}


def _render(question: str, claims: list[dict], evidence: list[dict], *,
            answer_markdown: str | None = None, intent=Intent.LOOKUP,
            uncertainties: list[str] | None = None, **kwargs):
    return compose.render(question, intent=intent, claims=claims, evidence=evidence,
                          uncertainties=uncertainties or [], locale="es",
                          answer_markdown=answer_markdown, **kwargs)


def _claim(text: str, *handles: str) -> dict:
    return {"text": text, "citations": list(handles)}


MARKDOWN = """## Síntesis

La demandante afirma que el pago se hizo el 13/12/2024 [E1]. El demandado sostiene que no
hubo pago alguno [E2]. Las dos versiones se presentan sin resolver el conflicto.

### Contraste

| Parte | Versión |
| --- | --- |
| Demandante | pago el 13/12/2024 [E1] |
| Demandado | no hubo pago [E2] |

> Ambas versiones constan en el expediente y no se resuelven aquí.
"""


def _ev() -> list[dict]:
    return [
        _doc("E1", 12, "La demandante afirma que el pago se hizo el 13/12/2024."),
        _seg("E2", "El demandado niega el pago en la audiencia."),
    ]


def _claims() -> list[dict]:
    return [
        _claim("La demandante afirma que el pago se hizo el 13/12/2024.", "E1"),
        _claim("El demandado niega el pago en la audiencia.", "E2"),
    ]


def test_ut_cmp_md_01_valid_markdown_is_preserved_and_renumbered():
    out = _render(QUESTION, _claims(), _ev(), answer_markdown=MARKDOWN)

    assert out.answer_kind == "direct"
    assert out.answer.startswith("## Síntesis")
    assert "[E1]" not in out.answer and "[E2]" not in out.answer
    assert "[1]" in out.answer and "[2]" in out.answer
    assert out.used_handles == ["E1", "E2"]
    # Títulos, tabla, negritas y blockquote sobreviven a la renumeración.
    assert "### Contraste" in out.answer
    assert "| Demandante | pago el 13/12/2024 [1] |" in out.answer
    assert "> Ambas versiones constan" in out.answer
    assert out.sections["evidence"] == []
    assert out.sections["direct"] and "## Síntesis" in out.sections["direct"][0]


def test_ut_cmp_md_02_sources_use_page_and_minute_with_speaker():
    out = _render(QUESTION, _claims(), _ev(), answer_markdown=MARKDOWN)

    assert "## Fuentes" in out.answer
    sources = out.answer.split("## Fuentes", 1)[1]
    assert "[1] demanda.pdf · p. 12" in sources
    assert "[2] audiencia.mp4" in sources and "37:08" in sources and "Rojas Melo" in sources
    assert out.sections["sources"][0].startswith("[1] ")
    # Las fuentes (y su numeración) coinciden con used_handles.
    assert len(out.sections["sources"]) == len(out.used_handles) == 2


def test_ut_cmp_md_03_unknown_handle_discards_the_whole_markdown():
    md = MARKDOWN.replace("[E2]", "[E99]", 1)
    out = _render(QUESTION, _claims(), _ev(), answer_markdown=md)

    assert "## Síntesis" not in out.answer
    assert "[99]" not in out.answer
    assert "[E99]" not in out.answer
    assert "**Fuentes**" in out.answer  # camino legado


def test_ut_cmp_md_04_more_handles_than_budget_discards_instead_of_truncating():
    ev = _ev() + [_doc("E3", 30, "Otra página sobre el pago.")]
    out = _render(QUESTION, _claims(), ev, answer_markdown=MARKDOWN.replace("[E2]", "[E3]"),
                  max_citations=1)

    assert "## Síntesis" not in out.answer
    assert "| Demandante |" not in out.answer
    assert len(out.used_handles) <= 1


def test_ut_cmp_md_05_markdown_without_headings_is_ignored():
    md = ("La demandante afirma que el pago se hizo el 13/12/2024 [E1]. "
          "El demandado sostiene que no hubo pago alguno [E2]. " * 3)
    out = _render(QUESTION, _claims(), _ev(), answer_markdown=md)

    assert "**Fuentes**" in out.answer
    assert "## Fuentes" not in out.answer


def test_ut_cmp_md_06_grouped_handles_are_renumbered():
    md = MARKDOWN.replace("[E2]", "[E1, E2]", 2)
    out = _render(QUESTION, _claims(), _ev(), answer_markdown=md)

    assert "[1, 2]" in out.answer
    assert out.used_handles == ["E1", "E2"]
    assert len(out.sections["sources"]) == 2


def test_ut_cmp_md_07_deep_without_direct_claims_has_no_partial_header():
    ev = [_doc("E1", 4, "El juzgado fijó audiencia para el 3 de marzo.")]
    claims = [_claim(ev[0]["text"], "E1")]
    out = _render(QUESTION, claims, ev, deep=True)

    assert out.answer_kind == "partial"
    assert "Encontré material relacionado" not in out.answer
    assert "**Evidencia**" in out.answer
    assert out.sections["direct"] == []


def test_ut_cmp_md_08_limits_only_when_uncertainties_exist():
    without = _render(QUESTION, _claims(), _ev(), answer_markdown=MARKDOWN)
    assert "## Límites" not in without.answer

    with_limits = _render(QUESTION, _claims(), _ev(), answer_markdown=MARKDOWN,
                          uncertainties=["No consta el segundo pagaré."])
    assert "## Límites" in with_limits.answer
    assert "- No consta el segundo pagaré." in with_limits.answer
    # El markdown del modelo se conserva antes de los bloques añadidos.
    assert with_limits.answer.index("## Síntesis") < with_limits.answer.index("## Límites")


def test_ut_cmp_md_09_markdown_survives_when_claims_do_not_answer_but_deep_is_on():
    ev = [_doc("E1", 4, "El juzgado fijó audiencia para el 3 de marzo.")] + _ev()[1:]
    claims = [_claim(ev[0]["text"], "E1")]
    out = _render(QUESTION, claims, ev, answer_markdown=MARKDOWN, deep=True)

    assert out.answer_kind == "direct"
    assert out.answer.startswith("## Síntesis")
    assert "Encontré material relacionado" not in out.answer


def test_ut_cmp_md_10_non_responding_claims_make_it_partial_but_keep_markdown():
    ev = [_doc("E1", 4, "El juzgado fijó audiencia para el 3 de marzo.")]
    claims = [_claim(ev[0]["text"], "E1")]
    md = MARKDOWN.replace("[E2]", "[E1]")
    out = _render(QUESTION, claims, ev, answer_markdown=md)

    assert out.answer_kind == "partial"
    assert out.answer.startswith("## Síntesis")
    assert "Encontré material relacionado" not in out.answer


def test_ut_cmp_md_11_json_to_markdown_end_to_end():
    """A1: JSON del modelo -> parse_and_validate -> compose.render."""
    ev = [
        _doc("E1", 12, "La demandante afirma que el pago se hizo el 13/12/2024."),
        _doc("E2", 20, "El demandado niega el pago en la contestación."),
        _seg("E3", "En la audiencia se discutió el comprobante del pago."),
    ]
    payload = {
        "answer_markdown": MARKDOWN + "\nEl comprobante se discutió en audiencia [E3].\n",
        "claims": [
            {"text": "La demandante afirma que el pago se hizo el 13/12/2024.", "citations": ["E1"]},
            {"text": "El demandado niega el pago en la audiencia.", "citations": ["E2"]},
            {"text": "El comprobante se discutió en audiencia.", "citations": ["E3"]},
        ],
        "uncertainties": [],
        "answer_kind": "partial",
    }
    validated = answering.parse_and_validate(json.dumps(payload), ev)
    assert validated["answer_markdown"] and len(validated["claims"]) == 3

    out = _render(QUESTION, validated["claims"], ev,
                  answer_markdown=validated["answer_markdown"],
                  uncertainties=validated["uncertainties"])

    assert "## S" in out.answer and "### Contraste" in out.answer
    assert "[E1]" not in out.answer
    assert "[1]" in out.answer and "[2]" in out.answer and "[3]" in out.answer
    assert out.used_handles == ["E1", "E2", "E3"]
    assert out.answer_kind == "direct"


def test_ut_cmp_md_12_fact_handles_are_renumbered_too():
    """Los hechos sembrados llegan con handles `F#`: no pueden quedar literales."""
    ev = [
        _doc("F1", 2, "El ejecutado alegó la capitalización de intereses.", "providencia.pdf"),
        _doc("E2", 10, "La demandada propuso la excepción de usura.", "contestacion.pdf"),
    ]
    md = "## Síntesis\n\nEl ejecutado alegó la capitalización [F1]. La demandada propuso usura [E2].\n"
    claims = [
        _claim("El ejecutado alegó la capitalización de intereses.", "F1"),
        _claim("La demandada propuso la excepción de usura.", "E2"),
    ]
    out = _render(QUESTION, claims, ev, answer_markdown=md)

    assert "[F1]" not in out.answer
    assert "[1]" in out.answer and "[2]" in out.answer
    assert out.used_handles == ["F1", "E2"]
    assert "[1] providencia.pdf · p. 2" in out.answer
