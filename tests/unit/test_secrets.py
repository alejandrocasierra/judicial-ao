"""Cifrado en reposo y barrera de escritura de tools."""
from __future__ import annotations

import pytest

from app.core import secrets as secret_store
from app.services import case_tools

pytestmark = pytest.mark.unit


def test_secret_roundtrip_y_legacy():
    token = secret_store.encrypt("sk-prueba-123")
    assert token and token.startswith("enc:")
    assert secret_store.decrypt(token) == "sk-prueba-123"
    # Valor legacy en claro: se acepta para migrar sin downtime.
    assert secret_store.decrypt("sk-legacy") == "sk-legacy"
    assert secret_store.encrypt(token) == token


def test_write_confirm_requires_server_context():
    with pytest.raises(PermissionError, match="WRITE_TOOL_REQUIRES_SERVER_CONFIRMATION"):
        case_tools.execute(
            None, "case", "correct_ocr_page",
            {"document_id": "x", "page_number": 1, "new_text": "y", "confirm": True},
            ctx=case_tools.ToolContext(org_id="o", actor_id="u"),
        )


def test_write_permission_map_is_central():
    assert case_tools.required_permission("correct_ocr_page") == "document.upload"
    assert case_tools.required_permission("correct_transcript_segment") == "media.upload"
    assert case_tools.required_permission("suggest_reprocess", {"media_id": "m"}) == "media.upload"
    assert case_tools.required_permission("search_case") == "ai.query"
