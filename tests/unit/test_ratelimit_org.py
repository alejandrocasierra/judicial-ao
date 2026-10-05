"""UT-RL — rate limiting por organización."""
import pytest

from app.core.errors import AppError
from app.services import ratelimit

pytestmark = pytest.mark.unit


def test_ut_rl_01_org_check_allows_under_limit():
    ratelimit.reset()
    ratelimit.check_org("query", "org-test")
    ratelimit.check_org("query", "org-test")


def test_ut_rl_02_org_check_blocks_over_limit(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(ratelimit, "_org_limit", lambda _bucket: 1)
    ratelimit.check_org("query", "org-limit")
    with pytest.raises(AppError, match="RATE_LIMITED"):
        ratelimit.check_org("query", "org-limit")


def test_ut_rl_03_unknown_bucket_is_noop():
    ratelimit.reset()
    ratelimit.check_org("unknown", "org-test")
