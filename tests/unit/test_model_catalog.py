"""UT-MCAT — construcción de URLs del catálogo dinámico de modelos."""
import pytest

from app.services import model_catalog as mc

pytestmark = pytest.mark.unit


def test_ut_mcat_openai_compatible_urls():
    assert mc._openai_models_url("https://api.openai.com") == "https://api.openai.com/v1/models"
    assert mc._openai_models_url("https://api.openai.com/v1") == "https://api.openai.com/v1/models"
    assert mc._openai_models_url("https://api.deepseek.com/") == "https://api.deepseek.com/v1/models"
    # El mount compat-con-OpenAI de Gemini también sirve para listar.
    assert mc._openai_models_url("https://generativelanguage.googleapis.com/v1beta/openai") == \
        "https://generativelanguage.googleapis.com/v1beta/openai/models"


def test_ut_mcat_anthropic_url():
    assert mc._anthropic_models_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/models"
    assert mc._anthropic_models_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/models"


def test_ut_mcat_gemini_url():
    assert mc._gemini_models_url("https://generativelanguage.googleapis.com/v1beta/openai") == \
        "https://generativelanguage.googleapis.com/v1beta/models"
    assert mc._gemini_models_url("https://generativelanguage.googleapis.com/v1beta") == \
        "https://generativelanguage.googleapis.com/v1beta/models"


def test_ut_mcat_filters_non_chat_models():
    assert mc._is_chat_model("gpt-5")
    assert mc._is_chat_model("deepseek-reasoner")
    assert mc._is_chat_model("gemini-3.8-flash")
    assert not mc._is_chat_model("text-embedding-3-large")
    assert not mc._is_chat_model("tts-1")
    assert not mc._is_chat_model("whisper-1")
    assert not mc._is_chat_model("dall-e-3")
    assert not mc._is_chat_model("sora-2")
    assert not mc._is_chat_model("gemini-3.8-flash-tts")
    assert not mc._is_chat_model("gemini-3.8-live")
    assert not mc._is_chat_model("veo-3.1-generate-preview")
    assert not mc._is_chat_model("gemini-robotics-er-2-preview")
