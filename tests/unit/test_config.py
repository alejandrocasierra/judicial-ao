"""UT-CFG — configuración sin valores quemados."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings

pytestmark = pytest.mark.unit


def test_ut_cfg_01_no_field_has_default():
    with_defaults = [n for n, f in Settings.model_fields.items() if not f.is_required()]
    assert with_defaults == [], f"campos con default (deben venir de entorno): {with_defaults}"


def test_ut_cfg_02_missing_variable_fails(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def _base(monkeypatch, **over):
    import os
    env = {k: v for k, v in os.environ.items()}
    env.update(over)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_ut_cfg_03_production_forbids_fake_llm_and_basic_scanner(monkeypatch):
    _base(monkeypatch, APP_ENV="production")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_ut_cfg_04_weak_jwt_secret_rejected(monkeypatch):
    _base(monkeypatch, JWT_SECRET="short")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_ut_cfg_05_real_llm_requires_key(monkeypatch):
    _base(monkeypatch, LLM_PROVIDER="anthropic", LLM_API_KEY="__SET_ME__")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


_PRODUCTION_OK = {"APP_ENV": "production", "LLM_PROVIDER": "anthropic", "LLM_API_KEY": "clave-real-de-produccion",
                  "LLM_MODEL": "modelo-real", "MALWARE_SCANNER": "clamav", "RATE_LIMIT_BACKEND": "redis",
                  "CELERY_TASK_ALWAYS_EAGER": "false"}


def test_ut_cfg_06_production_forbids_eager_celery(monkeypatch):
    """En producción el modo eager ejecutaría los jobs dentro de la API: prohibido."""
    _base(monkeypatch, **{**_PRODUCTION_OK, "CELERY_TASK_ALWAYS_EAGER": "true"})
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_ut_cfg_07_production_with_real_backends_boots(monkeypatch):
    """Control: con backends reales y eager=false, la configuración de producción es válida."""
    _base(monkeypatch, **_PRODUCTION_OK)
    assert Settings(_env_file=None).APP_ENV == "production"


def test_ut_cfg_08_stale_threshold_must_exceed_job_time_limit(monkeypatch):
    """Si el sweeper barre antes de que un job legítimo pueda terminar, lo ejecutaría dos veces."""
    _base(monkeypatch, JOB_STALE_MINUTES="30", JOB_TIME_LIMIT_SECONDS="3600")  # 30*60 = 1800 <= 3600
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_ut_cfg_09_stale_threshold_at_double_time_limit_is_valid(monkeypatch):
    """Control: el umbral a 2x del time limit es válido en cualquier entorno."""
    _base(monkeypatch, JOB_STALE_MINUTES="120", JOB_TIME_LIMIT_SECONDS="3600")
    s = Settings(_env_file=None)
    assert s.JOB_STALE_MINUTES == 120 and s.JOB_TIME_LIMIT_SECONDS == 3600
