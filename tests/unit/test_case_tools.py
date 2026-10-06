"""UT-CT — capa de tools compartida del chat IA (case_tools)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import agent, agent_tools, answering, case_tools
from app.services.case_tools import read as ct_read
from app.services.case_tools import write as ct_write

pytestmark = pytest.mark.unit

PLAN_TOOLS = ["search_case", "read_document", "get_document_page", "get_document_markdown",
              "search_transcript_by_time", "get_file", "list_case_files", "graph_query", "graph_neighbors",
              "find_person", "get_timeline", "get_video_segment", "search_transcripts", "list_speakers",
              "list_people_by_role", "correct_ocr_page", "correct_transcript_segment", "rename_speaker",
              "merge_speakers", "suggest_reprocess", "list_low_confidence_pages", "reprocess_low_confidence",
              "event_relations", "process_path"]


def test_ut_ct_01_mmss():
    assert ct_read.mmss(842000) == "14:02"
    assert ct_read.mmss(3661000) == "1:01:01"
    assert ct_read.mmss(None) == ""


def test_ut_ct_02_registry_contains_plan_tools():
    for name in PLAN_TOOLS:
        assert name in case_tools.TOOL_REGISTRY, name
    assert case_tools.TOOL_REGISTRY["correct_ocr_page"].kind == "write"
    assert case_tools.TOOL_REGISTRY["suggest_reprocess"].kind == "write"
    assert case_tools.TOOL_REGISTRY["search_case"].kind == "read"
    # Formato histórico usado por builtin_agents.tool_names()
    assert set(PLAN_TOOLS) <= set(agent_tools.TOOL_DESCRIPTIONS)


def test_ut_ct_03_merge_items_dedupes_and_rehandles():
    a = [{"handle": "E1", "document_id": "d1", "page_number": 1, "text": "x"},
         {"handle": "E2", "document_id": "d2", "page_number": 2, "text": "y"}]
    b = [{"handle": "E1", "document_id": "d1", "page_number": 1, "text": "x"},
         {"handle": "E2", "media_id": "m1", "segment_id": "s1", "start_ms": 5, "text": "z"}]
    out = answering.merge_items(a, b)
    assert [it["handle"] for it in out] == ["E1", "E2", "E3"]
    assert out[0]["document_id"] == "d1" and out[2]["media_id"] == "m1"


def test_ut_ct_04_file_cards_deduped():
    evidence = [
        {"source_type": "file", "kind": "document", "document_id": "d1", "name": "a.pdf",
         "download_path": "/cases/c/documents/d1/download", "view_path": "/cases/c/documents/d1/download"},
        {"source_type": "file", "kind": "document", "document_id": "d1", "name": "a.pdf"},
        {"source_type": "file", "kind": "media", "media_id": "m1", "name": "v.mp4"},
        {"source_type": "document_page", "document_id": "d9", "text": "no es tarjeta"},
    ]
    cards = agent._file_cards(evidence)
    assert len(cards) == 2
    assert cards[0]["document_id"] == "d1" and cards[1]["media_id"] == "m1"


def test_ut_ct_05_load_agent_prompt_merges_skills(monkeypatch):
    monkeypatch.setattr("app.core.db.one", lambda _c, _sql, **_p: {"system_prompt": "BASE", "skills": ["s1", "s2", "s3"]})
    monkeypatch.setattr("app.core.db.rows", lambda _c, _sql, **_p: [
        {"name": "Grill", "system_prompt": "P1 grill"},
        {"name": "Vacia", "system_prompt": "   "},
        {"name": "Video", "system_prompt": "P3 video"},
    ])
    out = agent.load_agent_prompt(MagicMock(), "agent-1")
    assert out.startswith("BASE")
    assert "[Skill: Grill]" in out and "P1 grill" in out
    assert "[Skill: Video]" in out and "Vacia" not in out


def test_ut_ct_06_agent_loop_declares_attachments(monkeypatch):
    captured = {}

    class CapLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, _system, user):
            if self.calls == 0:
                captured["first_user"] = user
            txt = ['{"thought": "leer", "tool": "read_document", "arguments": {"document_id": "d1"}}',
                   '{"thought": "listo", "done": true}',
                   '{"claims": [{"text": "Dice algo.", "citations": ["E1"]}], "uncertainties": []}'][self.calls]
            self.calls += 1
            return SimpleNamespace(text=txt, provider="fake", model="fake", tokens_in=1, tokens_out=1)

    monkeypatch.setattr(agent, "get_llm", lambda: CapLLM())
    monkeypatch.setattr(agent.agent_tools, "execute", lambda _c, _case, _t, _a: [
        {"handle": "E1", "source_type": "document_page", "document_id": "d1", "page_number": 1,
         "text": "Dice algo importante."}])
    agent.run_agent_query(MagicMock(), "case", "¿Qué dice?", "es",
                          attachments=[{"kind": "document", "id": "d1", "name": "001.pdf"}])
    assert "<attached_files>" in captured["first_user"]
    assert "001.pdf" in captured["first_user"] and "d1" in captured["first_user"]


def test_ut_ct_07_invalid_ids_return_error_without_touching_db():
    conn = MagicMock()
    for tool, args in [("get_file", {"document_id": "no-es-uuid"}),
                       ("get_video_segment", {"media_id": "x", "segment_id": "y"}),
                       ("graph_query", {"start_node_id": "x"}),
                       ("graph_neighbors", {"node_id": "x"}),
                       ("search_transcript_by_time", {"media_id": "x", "query": "tema"}),
                       ("read_document", {"document_id": "x"}),
                       ("find_person", {"name": ""})]:
        out = case_tools.execute(conn, "case", tool, args, ctx=case_tools.ToolContext())
        assert out and out[0]["source_type"] == "error", tool
    conn.execute.assert_not_called()


def _fake_page_select(old_text: str = "Texto OCR original."):
    return {"id": "page-1", "text": old_text, "filename": "001.pdf"}


def test_ut_ct_08_correct_ocr_requires_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(ct_write, "one", lambda _c, sql, **_p: calls.append(sql) or _fake_page_select())
    out = case_tools.execute(MagicMock(), "case", "correct_ocr_page",
                             {"document_id": "11111111-1111-1111-1111-111111111111", "page_number": 3,
                              "new_text": "Texto corregido."}, ctx=case_tools.ToolContext())
    assert out[0]["source_type"] == "correction_preview"
    assert out[0]["requires_confirmation"] is True
    assert out[0]["old_text"] == "Texto OCR original."
    assert not any("UPDATE" in sql for sql in calls), "sin confirm no puede escribir"


def test_ut_ct_09_correct_ocr_confirm_propagates(monkeypatch):
    captured: dict = {}

    def fake_one(_c, sql, **params):
        if "FROM document_pages" in sql:
            return _fake_page_select()
        if "UPDATE document_pages" in sql:
            captured["update"] = params
            return {"id": "page-1"}
        if "INSERT INTO reviews" in sql:
            captured["review"] = params
            return {"id": "rev-1"}
        return None

    monkeypatch.setattr(ct_write, "one", fake_one)
    monkeypatch.setattr(ct_write.ocr_lexicon, "learn_terms", lambda _c, _o, _t: 4)
    monkeypatch.setattr(ct_write.audit, "record", lambda *a, **k: captured.setdefault("audit", k))
    monkeypatch.setattr(ct_write, "_reindex_document", lambda *a: captured.setdefault("reindexed", True) or True)
    monkeypatch.setattr(ct_write, "_enqueue_graph", lambda *a: captured.setdefault("graph", True))
    ctx = case_tools.ToolContext(org_id="org-1", actor_id="user-1")
    out = case_tools.execute(MagicMock(), "case-1", "correct_ocr_page",
                             {"document_id": "11111111-1111-1111-1111-111111111111", "page_number": 3,
                              "new_text": "Texto corregido.", "reason": "el usuario lo dijo", "confirm": True},
                             ctx=ctx)
    assert out[0]["source_type"] == "correction_result"
    assert captured["update"]["t"] == "Texto corregido." and captured["update"]["u"] == "user-1"
    review = captured["review"]
    assert json.loads(review["orig"]) == {"text": "Texto OCR original."}
    assert json.loads(review["human"]) == {"text": "Texto corregido."}
    assert review["r"] == "user-1" and review["o"] == "org-1"
    # La propagación se difiere a post-commit (evita el auto-deadlock entre conexiones).
    assert not captured.get("reindexed") and len(ctx.post_commit) == 2
    case_tools.drain_post_commit(ctx)
    assert captured.get("reindexed") and captured.get("graph") and captured.get("audit")


def test_ut_ct_10_correct_ocr_rejects_identical_text(monkeypatch):
    monkeypatch.setattr(ct_write, "one", lambda _c, _sql, **_p: _fake_page_select("igual"))
    out = case_tools.execute(MagicMock(), "case", "correct_ocr_page",
                             {"document_id": "11111111-1111-1111-1111-111111111111", "page_number": 1,
                              "new_text": "igual", "confirm": True},
                             ctx=case_tools.ToolContext(org_id="o", actor_id="u"))
    assert out[0]["source_type"] == "error" and "idéntico" in out[0]["text"]


def test_ut_ct_11_write_tools_need_identity(monkeypatch):
    monkeypatch.setattr(ct_write, "one", lambda _c, _sql, **_p: _fake_page_select())
    out = case_tools.execute(MagicMock(), "case", "correct_ocr_page",
                             {"document_id": "11111111-1111-1111-1111-111111111111", "page_number": 1,
                              "new_text": "otro texto", "confirm": True},
                             ctx=case_tools.ToolContext())  # sin org/actor
    assert out[0]["source_type"] == "error" and "identidad" in out[0]["text"]


def test_ut_ct_13_speaker_candidate():
    from app.services.case_tools.read import _speaker_candidate
    assert _speaker_candidate("¿En qué minuto habló la doctora Paola?") == "Paola"
    assert _speaker_candidate("Paola") == "Paola"
    assert _speaker_candidate("qué se dijo sobre el pago") == "pago"
    assert _speaker_candidate("") is None


def test_ut_ct_12_suggest_reprocess_diagnostic(monkeypatch):
    def fake_one(_c, sql, **_p):
        if "FROM documents" in sql:
            return {"id": "d1", "filename": "001.pdf", "ocr_mode": "basico",
                    "processing_status": "OCR_COMPLETED", "page_count": 10}
        return {"pages": 10, "avg_conf": 0.61, "needs_review": 3, "human_corrected": 0}

    monkeypatch.setattr(ct_write, "one", fake_one)
    monkeypatch.setattr(ct_write, "rows", lambda _c, _sql, **_p: [{"mode": "basico", "pages": 10, "avg_conf": 0.61}])
    out = case_tools.execute(MagicMock(), "case", "suggest_reprocess",
                             {"document_id": "11111111-1111-1111-1111-111111111111"},
                             ctx=case_tools.ToolContext())
    assert out[0]["source_type"] == "correction_preview"
    assert "Diagnóstico" in out[0]["text"] and out[0]["recommended_mode"] == "document_ai"
    assert out[0]["requires_confirmation"] is True


def test_ut_ct_14_name_candidates_filters_roles_and_numbers():
    """`_name_candidates` extrae nombres propios y descarta cargos/números en letras."""
    out = ct_read._name_candidates(
        "Firmado por ALBA LUCY COCK ÁLVAREZ, JUEZ CIRCUITO. Valor: CIENTO CINCUENTA MILLONES PESOS.")
    assert "ALBA LUCY COCK ÁLVAREZ" in out
    assert all("JUEZ CIRCUITO" != n and "MILLONES" not in n for n in out)


def test_ut_ct_15_role_signature_matches_uses_signature_not_addressee():
    """`_role_signature_matches` toma la firma (nombre→cargo), no al destinatario 'SEÑOR JUEZ'."""
    text = "SEÑOR JUEZ\nJUAN CARLOS PEREZ\n\nFirmado: ALBA LUCY COCK ÁLVAREZ\nJUEZ"
    matches = ct_read._role_signature_matches(text, ("juez",))
    names = [n for n, _snippet in matches]
    assert any("ALBA LUCY COCK" in n for n in names)
    assert not any("JUAN CARLOS PEREZ" in n for n in names)
    assert matches and matches[0][1]  # incluye el fragmento de la firma
