"""UT-ST / UT-JUR — máquinas de estado y jurisdicción."""
import pytest

from app.domain import states
from app.domain.jurisdiction import normalize_citations, validate_case_number

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("a,b,ok", [("CREATED", "INGESTING", True), ("PROCESSING", "READY", False),
                                    ("REVIEWING", "READY", True), ("ARCHIVED", "PROCESSING", False), ("READY", "ARCHIVED", True)])
def test_ut_st_01_case_transitions(a, b, ok):
    assert states.can_transition(states.CASE_TRANSITIONS, a, b) is ok


def test_ut_st_02_archived_and_succeeded_are_terminal():
    assert states.CASE_TRANSITIONS["ARCHIVED"] == set() and states.JOB_TRANSITIONS["SUCCEEDED"] == set()


@pytest.mark.parametrize("a,b,ok", [("RUNNING", "QUEUED", True), ("RETRYING", "QUEUED", True),
                                    ("QUEUED", "QUEUED", False), ("SUCCEEDED", "QUEUED", False),
                                    ("FAILED", "QUEUED", False), ("CANCELLED", "QUEUED", False)])
def test_ut_st_04_job_requeue_transitions(a, b, ok):
    """El sweeper sólo puede devolver a QUEUED jobs huérfanos en RUNNING/RETRYING."""
    assert states.can_transition(states.JOB_TRANSITIONS, a, b) is ok


@pytest.mark.parametrize("err,policy", [("network_error", "retry"), ("model_timeout", "retry"),
                                        ("invalid_pdf", "manual_review"), ("ambiguous_identity", "manual_review")])
def test_ut_st_03_retry_policy(err, policy):
    assert states.retry_policy(err) == policy


@pytest.mark.parametrize("num,ok", [("11001310300120240012300", True), ("1100131030012024001230", False),
                                    ("1100131030012024001230A", False), ("' OR 1=1 --", False)])
def test_ut_jur_01_colombian_case_number(num, ok):
    assert validate_case_number("co", num) is ok


def test_ut_jur_02_citation_normalization_is_canonical():
    forms = ["Art. 1602 C.C.", "Artículo 1602 del Código Civil", "C.C., art. 1602"]
    results = [normalize_citations("co", f) for f in forms]
    assert all(r == [{"jurisdiction": "co", "code": "codigo_civil", "article": "1602", "version": None}] for r in results)


def test_ut_jur_03_unknown_jurisdiction_invalid():
    assert validate_case_number("xx", "123") is False
