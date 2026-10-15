"""UT-PARTY — roles de parte (catálogo) y lado del apoderado."""
import pytest

from app.services import party_extraction as pe
from app.services import party_roles as pr

pytestmark = pytest.mark.unit


def test_party_roles_include_sides():
    """El catálogo por defecto trae apoderado por parte (demandante/demandada)."""
    codes = {c for c, _l, _o in pr.DEFAULT_ROLES}
    assert "attorney_claimant" in codes
    assert "attorney_defendant" in codes
    labels = {c: l for c, l, _o in pr.DEFAULT_ROLES}
    assert labels["attorney_claimant"] == "Apoderado parte demandante"
    assert labels["attorney_defendant"] == "Apoderado parte demandada"


def test_role_from_header_attorney_side():
    """El encabezado del apoderado identifica el lado de la parte."""
    assert pe._role_from_header("APODERADO DE LA PARTE DEMANDANTE") == "attorney_claimant"
    assert pe._role_from_header("Apoderada del demandante") == "attorney_claimant"
    assert pe._role_from_header("APODERADO DE LA PARTE ACTORA") == "attorney_claimant"
    assert pe._role_from_header("Apoderado de la parte demandada") == "attorney_defendant"
    assert pe._role_from_header("APODERADA DEL DEMANDADO") == "attorney_defendant"
    assert pe._role_from_header("Apoderado de la parte ejecutada") == "attorney_defendant"


def test_role_from_header_generic_attorney_and_others():
    """Sin lado explícito sigue siendo 'Apoderado'; los demás roles no se rompen."""
    assert pe._role_from_header("Apoderado") == "attorney"
    assert pe._role_from_header("Abogada") == "attorney"
    assert pe._role_from_header("Demandante") == "claimant"
    assert pe._role_from_header("Demandado") == "defendant"
    assert pe._role_from_header("texto cualquiera") is None
