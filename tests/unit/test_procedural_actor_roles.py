"""UT-PROCEDURAL-ACTOR-ROLES — mapeo entre actor textual y rol de parte."""
import pytest

from app.services import procedural_actor_roles as par

pytestmark = pytest.mark.unit


def test_role_code_for_actor_maps_canonical_actors():
    assert par.role_code_for_actor("demandante") == "claimant"
    assert par.role_code_for_actor("demandado") == "defendant"
    assert par.role_code_for_actor("apoderado_demandante") == "attorney_claimant"
    assert par.role_code_for_actor("apoderado_demandado") == "attorney_defendant"
    assert par.role_code_for_actor("tercero") == "third_party"


def test_role_code_for_actor_ignores_non_party_actors():
    assert par.role_code_for_actor("juzgado") is None
    assert par.role_code_for_actor("secretario") is None
    assert par.role_code_for_actor("fiscal") is None
    assert par.role_code_for_actor("otro") is None


def test_role_code_for_actor_is_case_and_whitespace_tolerant():
    assert par.role_code_for_actor("Demandante") == "claimant"
    assert par.role_code_for_actor("  DEMANDADO  ") == "defendant"
    assert par.role_code_for_actor("") is None
    assert par.role_code_for_actor(None) is None
