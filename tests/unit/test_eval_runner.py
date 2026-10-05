"""UT-EVL — golden eval runner."""
import pytest

from scripts.run_evals import _citation_matches, _evaluate

pytestmark = pytest.mark.unit


def test_ut_evl_01_citation_matches():
    item = {"filename": "0001 doc.pdf", "page_number": 5, "source_type": "document_page"}
    assert _citation_matches(item, {"filename_contains": "doc", "page_number": 5})
    assert not _citation_matches(item, {"filename_contains": "other", "page_number": 5})
    assert not _citation_matches(item, {"filename_contains": "doc", "page_number": 6})


def test_ut_evl_02_evaluate_retrieval_only_pass():
    case = {"must_cite": [{"filename_contains": "doc", "page_number": 5}]}
    result = {"evidence": [{"filename": "0001 doc.pdf", "page_number": 5, "source_type": "document_page"}],
              "evidence_count": 1, "claims": [], "answer": ""}
    checks = _evaluate(case, result, retrieval_only=True)
    assert checks["pass"] is True
    assert checks["retrieval_recall"] == 1.0


def test_ut_evl_03_evaluate_abstain():
    case = {"must_abstain": True}
    result = {"evidence": [], "evidence_count": 0, "claims": [], "answer": ""}
    checks = _evaluate(case, result)
    assert checks["abstain"] is True
    assert checks["pass"] is True


def test_ut_evl_04_evaluate_full_checks():
    case = {"must_cite": [{"filename_contains": "doc"}], "forbidden_statements": ["mentira"],
            "expected_contains": ["demanda"]}
    result = {"evidence": [{"filename": "doc.pdf", "page_number": 1, "source_type": "document_page", "handle": "E1"}],
              "evidence_count": 1, "claims": [{"text": "Se presentó demanda.", "citations": ["E1"]}],
              "citations": [{"handle": "E1"}], "answer": "Se presentó demanda."}
    checks = _evaluate(case, result)
    assert checks["pass"] is True
