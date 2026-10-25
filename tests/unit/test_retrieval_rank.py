"""UT-RNK — reranker determinista, dedupe y diversidad (contrato §1.4)."""
import pytest

from app.services import retrieval_rank
from app.services.intent import Intent
from app.services.retrieval_plan import RetrievalPlan

pytestmark = pytest.mark.unit


def _plan(**overrides) -> RetrievalPlan:
    base = dict(
        question="¿Qué evidencia hay sobre pagos?",
        queries=("¿Qué evidencia hay sobre pagos?",),
        candidate_k=40,
        final_k=4,
        doc_quota=0.0,
        max_per_file=5,
        max_per_page=5,
        enable_rerank=True,
        expand_neighbors=False,
        use_graph=False,
        embedding_model=None,
        embedding_version=None,
        strict_embedding_version=False,
    )
    base.update(overrides)
    return RetrievalPlan(**base)


def _doc(ident: str, filename: str, page: int, text: str, *, score: float = 1.0,
         document_id: str | None = None, ocr: str | None = None, metadata: dict | None = None) -> dict:
    meta = dict(metadata or {})
    if ocr:
        meta["ocr_mode"] = ocr
    return {
        "id": ident, "document_id": document_id or f"DOC-{filename}", "page_number": page,
        "media_id": None, "start_ms": None, "end_ms": None, "filename": filename,
        "text": text, "metadata": meta, "score": score, "source_type": "document_page",
    }


def _seg(ident: str, media: str, speaker: str, start: int, end: int, text: str,
         *, score: float = 1.0) -> dict:
    return {
        "id": ident, "document_id": None, "page_number": None, "media_id": media,
        "start_ms": start, "end_ms": end, "filename": f"{media}.mp4", "speaker": speaker,
        "text": text, "metadata": {}, "score": score, "source_type": "transcript_segment",
    }


def test_rnk_01_dedupe_keeps_the_best_ocr_score():
    items = [
        _doc("bajo", "doc.pdf", 3, "pago", score=0.4, ocr="basico"),
        _doc("alto", "doc.pdf", 3, "pago", score=0.9, ocr="document_ai"),
    ]
    out = retrieval_rank.apply("pagos", items, plan=_plan(), intent=Intent.LOOKUP)
    assert len(out) == 1
    assert out[0]["id"] == "alto"


def test_rnk_02_dedupe_prefers_document_ai_on_tie():
    items = [
        _doc("basico", "doc.pdf", 3, "pago", score=0.8, ocr="basico"),
        _doc("avanzado", "doc.pdf", 3, "pago", score=0.8, ocr="document_ai"),
    ]
    out = retrieval_rank.apply("pagos", items, plan=_plan(), intent=Intent.LOOKUP)
    assert len(out) == 1
    assert out[0]["id"] == "avanzado"


def test_rnk_03_merges_contiguous_segments_of_the_same_speaker():
    items = [
        _seg("s1", "M1", "Ana", 1000, 4000, "el pago fue"),
        _seg("s2", "M1", "Ana", 5000, 8000, "el pago fue de ochenta millones"),
    ]
    out = retrieval_rank.apply("pago", items, plan=_plan(), intent=Intent.LOOKUP)
    assert len(out) == 1
    assert out[0]["start_ms"] == 1000
    assert "ochenta millones" in out[0]["text"]


def test_rnk_04_does_not_merge_different_speakers():
    items = [
        _seg("s1", "M1", "Ana", 1000, 4000, "el pago fue"),
        _seg("s2", "M1", "Luis", 5000, 8000, "el pago no fue"),
    ]
    out = retrieval_rank.apply("pago", items, plan=_plan(), intent=Intent.LOOKUP)
    assert len(out) == 2


def test_rnk_05_does_not_merge_segments_far_apart():
    items = [
        _seg("s1", "M1", "Ana", 1000, 4000, "el pago fue"),
        _seg("s2", "M1", "Ana", 9000, 12000, "el pago no fue"),
    ]
    out = retrieval_rank.apply("pago", items, plan=_plan(), intent=Intent.LOOKUP)
    assert len(out) == 2


def test_rnk_06_document_prior_wins_for_documental_lookup():
    question = "¿Qué evidencia hay sobre pagos?"
    items = [
        _seg("audio", "M1", "Ana", 1000, 4000, "el pago consta en la demanda"),
        _doc("doc", "demanda.pdf", 4, "el pago consta en la demanda"),
    ]
    out = retrieval_rank.apply(question, items, plan=_plan(final_k=2), intent=Intent.LOOKUP)
    assert out[0]["id"] == "doc"


def test_rnk_07_transcript_prior_wins_for_testimony_question():
    question = "¿Qué dijo el testigo sobre el pago?"
    items = [
        _doc("doc", "demanda.pdf", 4, "el testigo dijo que el pago consta"),
        _seg("audio", "M1", "Ana", 1000, 4000, "el testigo dijo que el pago consta"),
    ]
    out = retrieval_rank.apply(question, items, plan=_plan(final_k=2), intent=Intent.LOOKUP)
    assert out[0]["id"] == "audio"


def test_rnk_08_coverage_dominates_raw_score():
    question = "¿Qué evidencia hay sobre pagos?"
    items = [
        _doc("cubre", "a.pdf", 1, "pago pago", score=0.1),
        _doc("ruido", "b.pdf", 1, "contrato contrato", score=1.0),
    ]
    out = retrieval_rank.apply(question, items, plan=_plan(final_k=2), intent=Intent.LOOKUP)
    assert out[0]["id"] == "cubre"


def test_rnk_09_max_per_file_is_enforced():
    items = [_doc(f"d{i}", "a.pdf", i, "pago", score=1.0) for i in range(1, 4)]
    out = retrieval_rank.apply("pagos", items, plan=_plan(final_k=5, max_per_file=2), intent=Intent.LOOKUP)
    assert len(out) == 2


def test_rnk_10_max_per_page_is_enforced():
    items = [
        _seg("s1", "M1", "Ana", 1000, 2000, "pago uno"),
        _seg("s2", "M1", "Ana", 5000, 6000, "pago dos"),
        _seg("s3", "M1", "Ana", 9000, 10000, "pago tres"),
    ]
    out = retrieval_rank.apply("pagos", items, plan=_plan(final_k=5, max_per_page=2), intent=Intent.LOOKUP)
    assert len(out) == 2


def test_rnk_11_mmr_prefers_diversity_over_a_near_duplicate():
    items = [
        _doc("a", "a.pdf", 1, "pago contrato demanda", score=1.0),
        _doc("b", "b.pdf", 1, "pago contrato demanda", score=0.95),
        _doc("c", "c.pdf", 1, "testigo audiencia declaracion", score=0.9),
    ]
    out = retrieval_rank.apply("pago contrato", items, plan=_plan(final_k=2), intent=Intent.LOOKUP)
    assert [it["id"] for it in out] == ["a", "c"]


def test_rnk_12_low_coverage_does_not_cut_and_shifts_weight_to_coverage():
    question = "pago contrato"
    cubre = _doc("cubre", "a.pdf", 1, "pago contrato", score=0.0)
    ruido = _doc("ruido", "b.pdf", 1, "otra cosa", score=1.0)
    normal_cubre = retrieval_rank.score_item(question, cubre, intent=Intent.LOOKUP, max_raw_score=1.0)
    normal_ruido = retrieval_rank.score_item(question, ruido, intent=Intent.LOOKUP, max_raw_score=1.0)
    low_cubre = retrieval_rank.score_item(question, cubre, intent=Intent.LOOKUP,
                                          max_raw_score=1.0, coverage=0.1)
    low_ruido = retrieval_rank.score_item(question, ruido, intent=Intent.LOOKUP,
                                          max_raw_score=1.0, coverage=0.1)
    assert (low_cubre - low_ruido) > (normal_cubre - normal_ruido)
    out = retrieval_rank.apply(question, [cubre, ruido], plan=_plan(final_k=4),
                               intent=Intent.LOOKUP, coverage=0.1)
    assert len(out) == 2


def test_rnk_13_neighbors_are_context_only():
    item = _doc("a", "a.pdf", 1, "pago", metadata={"neighbors": [{"text": "contexto"}]})
    with_neighbors = retrieval_rank.apply("pagos", [item],
                                          plan=_plan(expand_neighbors=True), intent=Intent.LOOKUP)
    assert with_neighbors[0]["neighbors"] == [{"text": "contexto"}]
    assert len(with_neighbors) == 1

    without = retrieval_rank.apply("pagos", [item],
                                   plan=_plan(expand_neighbors=False), intent=Intent.LOOKUP)
    assert "neighbors" not in without[0]


def test_rnk_14_handles_are_not_returned():
    item = _doc("a", "a.pdf", 1, "pago")
    item["handle"] = "E7"
    out = retrieval_rank.apply("pagos", [item], plan=_plan(), intent=Intent.LOOKUP)
    assert "handle" not in out[0]


def test_rnk_15_final_k_caps_the_result():
    items = [_doc(f"d{i}", f"{i}.pdf", 1, "pago", score=1.0) for i in range(10)]
    out = retrieval_rank.apply("pagos", items, plan=_plan(final_k=3), intent=Intent.LOOKUP)
    assert len(out) == 3


def test_rnk_16_empty_input():
    assert retrieval_rank.apply("pagos", [], plan=_plan(), intent=Intent.LOOKUP) == []


def test_rnk_17_scores_are_deterministic():
    items = [_doc("a", "a.pdf", 1, "pago", score=0.5),
             _doc("b", "b.pdf", 1, "pago contrato", score=0.7)]
    first = retrieval_rank.apply("pago contrato", items, plan=_plan(), intent=Intent.LOOKUP)
    second = retrieval_rank.apply("pago contrato", items, plan=_plan(), intent=Intent.LOOKUP)
    assert [it["id"] for it in first] == [it["id"] for it in second]
    assert all("rank_score" in it for it in first)

# --- F2 · R5: cuota de transcripciones (ASR) ----------------------------------

def _docs_and_segments(count: int = 10) -> list[dict]:
    docs = [_doc(f"d{i}", f"{i}.pdf", 1, "pago capitalización", score=1.0) for i in range(count)]
    segs = [_seg(f"s{i}", "M1", f"Hablante {i}", i * 60000, i * 60000 + 30000,
                 "pago capitalización", score=0.9) for i in range(count)]
    return docs + segs


def test_rnk_18_transcript_quota_reserves_slots_for_audio():
    """A5: 10 documentos + 10 transcripciones, final_k=10, cuota 0.4 → ≥4 audiencias."""
    plan = _plan(final_k=10, doc_quota=0.4, transcript_quota=0.4,
                 max_per_file=10, max_per_page=10)
    out = retrieval_rank.apply("¿Qué evidencia hay sobre pagos?", _docs_and_segments(),
                               plan=plan, intent=Intent.LOOKUP)
    segments = [it for it in out if it["source_type"] == "transcript_segment"]
    assert len(out) == 10
    assert len(segments) >= 4


def test_rnk_19_without_transcripts_the_document_quota_still_fills():
    """A5: sin transcripciones no se reserva nada y se devuelven los 10 documentos."""
    plan = _plan(final_k=10, doc_quota=0.4, transcript_quota=0.4,
                 max_per_file=10, max_per_page=10)
    docs = [_doc(f"d{i}", f"{i}.pdf", 1, "pago", score=1.0) for i in range(10)]
    out = retrieval_rank.apply("¿Qué evidencia hay sobre pagos?", docs,
                               plan=plan, intent=Intent.LOOKUP)
    assert len(out) == 10
    assert all(it["source_type"] == "document_page" for it in out)


def test_rnk_20_transcript_quota_never_exceeds_final_k():
    plan = _plan(final_k=3, doc_quota=0.4, transcript_quota=0.9,
                 max_per_file=10, max_per_page=10)
    out = retrieval_rank.apply("¿Qué evidencia hay sobre pagos?", _docs_and_segments(),
                               plan=plan, intent=Intent.LOOKUP)
    assert len(out) == 3
    assert len([it for it in out if it["source_type"] == "transcript_segment"]) >= 1


def test_rnk_21_zero_transcript_quota_keeps_the_previous_behaviour():
    plan = _plan(final_k=4, doc_quota=0.0, transcript_quota=0.0,
                 max_per_file=10, max_per_page=10)
    out = retrieval_rank.apply("¿Qué evidencia hay sobre pagos?", _docs_and_segments(),
                               plan=plan, intent=Intent.LOOKUP)
    assert len(out) == 4


def test_rnk_22_plan_includes_transcript_quota_and_cache_key_is_stable():
    """A6: `as_dict` transporta la cuota; el mismo plan produce la misma clave."""
    from app.services import intent as intent_mod, retrieval_plan

    question = "¿Qué dijo el testigo en la audiencia sobre los pagos?"
    decision = intent_mod.classify(question)
    plan = retrieval_plan.build(question, decision)
    assert plan.transcript_quota == retrieval_plan.TRANSCRIPT_QUOTA
    as_dict = retrieval_plan.as_dict(plan)
    assert as_dict["transcript_quota"] == plan.transcript_quota
    assert retrieval_plan.as_dict(plan) == as_dict

    plain = retrieval_plan.build("¿Qué dice el contrato?",
                                 intent_mod.classify("¿Qué dice el contrato?"))
    assert plain.transcript_quota == 0.0
