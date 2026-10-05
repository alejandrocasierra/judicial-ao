"""Verificación semántica anti-alucinación (SSD §33).

Complementa el grounding léxico/cifras de answering.py con un juez semántico
(LLM) que clasifica cada claim de la respuesta frente a la evidencia citada.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

from app.core.config import get_settings
from app.providers.llm import get_llm


def _source_label(it: dict[str, Any]) -> str:
    if it.get("source_type") == "document_page" or it.get("document_id"):
        return f"document:{it.get('document_id')} page:{it.get('page_number')}"
    if it.get("source_type") == "transcript_segment" or it.get("media_id"):
        return f"media:{it.get('media_id')} {it.get('start_ms')}-{it.get('end_ms')}ms"
    return f"node:{it.get('node_id')}"


def _build_prompt(claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    blocks = []
    for it in evidence:
        blocks.append(f'<evidence id="{it["handle"]}" source="{html.escape(_source_label(it), quote=True)}">\n'
                      f'{html.escape(str(it.get("text") or ""), quote=False)}\n</evidence>')
    claim_blocks = []
    for cl in claims:
        claim_blocks.append(f'<claim citations="{",".join(cl.get("citations", []))}">{html.escape(cl["text"], quote=False)}</claim>')
    return "<claims>\n" + "\n".join(claim_blocks) + "\n</claims>\n\n" + "\n".join(blocks)


def verify(claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devuelve lista de verdicts extendida con las citas originales."""
    if not claims:
        return []
    s = get_settings()
    raw = (s.path(s.PROMPTS_DIR) / "verify_answer.v1.md").read_text(encoding="utf-8")
    system = raw.split("---", 2)[2].strip()
    user = _build_prompt(claims, evidence)
    result = get_llm().complete(system, user)
    txt = re.sub(r"^```(?:json)?|```$", "", result.text.strip(), flags=re.M).strip()
    try:
        data = json.loads(txt)
        verdicts = data.get("verdicts") or []
    except Exception:
        return []
    by_text = {cl["text"]: cl.get("citations", []) for cl in claims}
    for v in verdicts:
        v["citations"] = by_text.get(v.get("claim", ""), v.get("citations", []))
    return verdicts
