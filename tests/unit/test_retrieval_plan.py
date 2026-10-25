"""UT-RPL — plan de recuperación (contrato §1.3)."""
import pytest

from app.core.config import get_settings
from app.services import intent, retrieval_plan
from app.services.retrieval_plan import RetrievalPlan

pytestmark = pytest.mark.unit


def _decision(question: str) -> intent.IntentDecision:
    return intent.classify(question)


def _settings(**overrides):
    return get_settings().model_copy(update=overrides)


def test_rpl_01_candidate_k_is_at_least_four_times_final_k():
    s = _settings(RETRIEVAL_CANDIDATE_K=10, RETRIEVAL_FINAL_K=8)
    plan = retrieval_plan.build("¿Qué evidencia hay sobre pagos?", _decision("¿Qué evidencia hay sobre pagos?"), settings=s)
    assert plan.candidate_k == max(10, 4 * 8) == 32


def test_rpl_02_candidate_k_keeps_configured_value_when_larger():
    s = _settings(RETRIEVAL_CANDIDATE_K=50, RETRIEVAL_FINAL_K=4)
    plan = retrieval_plan.build("¿Qué evidencia hay sobre pagos?", _decision("¿Qué evidencia hay sobre pagos?"), settings=s)
    assert plan.candidate_k == 50


def test_rpl_03_queries_start_with_the_original_question():
    question = "¿Qué evidencia hay sobre pagos?"
    plan = retrieval_plan.build(question, _decision(question))
    assert plan.queries[0] == question
    assert len(plan.queries) > 1


def test_rpl_04_queries_are_normalized_unique():
    question = "¿Qué evidencia hay sobre pagos?"
    plan = retrieval_plan.build(question, _decision(question))
    keys = [q.lower() for q in plan.queries]
    assert len(keys) == len(set(keys))


def test_rpl_05_graph_only_for_lookup_and_analysis():
    for question, expected in [
        ("¿Qué evidencia hay sobre pagos?", True),
        ("compara la demanda con la contestación", True),
        ("¿Dónde aparece el pago?", False),
        ("¿Cuántos jueces intervinieron?", False),
        ("¿Por qué se llegó a la sentencia?", False),
        ("resume este archivo", False),
        ("corrige el hablante", False),
    ]:
        plan = retrieval_plan.build(question, _decision(question))
        assert plan.use_graph is expected, question


def test_rpl_06_settings_are_projected_into_the_plan():
    s = _settings(RETRIEVAL_FINAL_K=6, RETRIEVAL_MAX_PER_FILE=3, RETRIEVAL_MAX_PER_PAGE=1,
                  RETRIEVAL_DOC_QUOTA=0.5, RETRIEVAL_STRICT_EMBEDDING_VERSION=True)
    plan = retrieval_plan.build("¿Qué evidencia hay sobre pagos?", _decision("¿Qué evidencia hay sobre pagos?"), settings=s)
    assert plan.final_k == 6
    assert plan.max_per_file == 3
    assert plan.max_per_page == 1
    assert plan.doc_quota == 0.5
    assert plan.strict_embedding_version is True
    # Un placeholder de entorno nunca se usa como filtro de vectores.
    assert plan.embedding_model is None
    assert plan.embedding_version == s.PIPELINE_VERSION


def test_rpl_07_plan_is_frozen():
    plan = retrieval_plan.build("¿Qué evidencia hay sobre pagos?", _decision("¿Qué evidencia hay sobre pagos?"))
    with pytest.raises(Exception):
        plan.final_k = 99  # type: ignore[misc]


def test_rpl_08_replace_queries_returns_a_copy():
    plan = retrieval_plan.build("¿Qué evidencia hay sobre pagos?", _decision("¿Qué evidencia hay sobre pagos?"))
    expanded = retrieval_plan.replace_queries(plan, ["otra consulta", "y otra"])
    assert isinstance(expanded, RetrievalPlan)
    assert expanded.queries[0] == "otra consulta"
    assert plan.queries[0] == "¿Qué evidencia hay sobre pagos?"


def test_rpl_09_expand_neighbors_for_substantive_intents():
    lookup = retrieval_plan.build("¿Qué evidencia hay sobre pagos?", _decision("¿Qué evidencia hay sobre pagos?"))
    navigation = retrieval_plan.build("¿Dónde aparece el pago?", _decision("¿Dónde aparece el pago?"))
    assert lookup.expand_neighbors is True
    assert navigation.expand_neighbors is False


def test_rpl_10_history_context_reaches_the_queries():
    history = [{"role": "user", "content": "¿Qué pasó con el contrato?"}]
    question = "¿y los pagos?"
    plan = retrieval_plan.build(question, _decision(question), history=history)
    assert any("¿Qué pasó con el contrato?" in q for q in plan.queries)


def test_rpl_11_analysis_usa_el_presupuesto_profundo_por_defecto():
    """A8: el modo profundo necesita más evidencia y más páginas por archivo."""
    question = "compara la demanda con la contestación"
    plan = retrieval_plan.build(question, _decision(question), settings=get_settings())
    assert plan.final_k == 40
    assert plan.candidate_k == 240
    assert plan.max_per_file == 8


def test_rpl_12_lookup_usa_el_presupuesto_normal_por_defecto():
    question = "¿Qué evidencia hay sobre pagos?"
    plan = retrieval_plan.build(question, _decision(question), settings=get_settings())
    assert plan.final_k == 18
    assert plan.candidate_k == 80
    assert plan.max_per_file == 4
