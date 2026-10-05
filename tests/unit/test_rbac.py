"""UT-RBAC — permisos efectivos = rol org ∩ rol expediente."""
import pytest

from app.security import rbac

pytestmark = pytest.mark.unit


def test_ut_rbac_01_intersection_analyst_as_reviewer_cannot_review():
    perms = rbac.case_permissions("ANALYST", "REVIEWER")
    assert "ai.query" in perms and "review.write" not in perms and "document.download" not in perms


def test_ut_rbac_02_admin_has_everything_without_membership():
    assert rbac.case_permissions("ORG_ADMIN", None) == set(rbac.policy()["permissions"])


def test_ut_rbac_03_no_membership_no_access():
    assert rbac.case_permissions("LAWYER", None) == set()


def test_ut_rbac_04_read_only_cannot_write():
    p = rbac.case_permissions("READ_ONLY", "OWNER")
    assert p == {"case.read", "document.read", "media.read"}


def test_ut_rbac_05_only_admin_has_org_level_sensitive_perms():
    for role in rbac.policy()["org_roles"]:
        perms = rbac.org_permissions(role)
        if role != "ORG_ADMIN":
            assert not perms & {"legal_hold.manage", "evidence.deletion_request", "audit.read", "user.manage"}
