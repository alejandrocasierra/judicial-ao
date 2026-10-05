"""Clasificación documental con LLM económico + fallback heurístico (Fase 2)."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.providers.llm import get_llm

log = logging.getLogger(__name__)

PROMPT_FILE = Path("packages/prompts/classify_document.v1.md")
MAX_TEXT_CHARS = 5000
VALID_TYPES = {
    "demand", "payment_order", "ruling", "judgment", "appeal", "injunction",
    "hearing_record", "expert_report", "policy", "other/unknown",
}

# Clasificación heurística baseline; fallback cuando el LLM no está disponible o falla.
DOCUMENT_TYPE_HINTS: list[tuple[str, list[str]]] = [
    ("demand", ["demanda", "demandante", "demandado", "pretensiones"]),
    ("payment_order", ["mandamiento", "pago", "orden de pago"]),
    ("ruling", ["auto", "resuelve", "mérito"]),
    ("judgment", ["sentencia", "fallo", "condena", "absuelve"]),
    ("appeal", ["apelación", "recurso", "casación"]),
    ("injunction", ["medida cautelar", "secuestro", "embargo", "diligencia"]),
    ("hearing_record", ["audiencia", "acta", "juez", "secretario"]),
    ("expert_report", ["dictamen", "perito", "informe técnico"]),
    ("policy", ["póliza", "seguro", "aseguradora"]),
]


def classify_document_type(text: str) -> str:
    """Clasificación baseline por palabras clave."""
    lower = text.lower()
    for doc_type, hints in DOCUMENT_TYPE_HINTS:
        if any(h in lower for h in hints):
            return doc_type
    return "other/unknown"


def _load_prompt() -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    # Separa frontmatter YAML del cuerpo.
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def _extract_json(text: str) -> dict | None:
    """Extrae el primer objeto JSON de la respuesta del modelo."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def classify_document(text: str) -> tuple[str, str]:
    """Clasifica un documento. Devuelve (document_type, reason).

    Si el proveedor LLM es el fake de tests, usa la heurística baseline para
    mantener los tests deterministas. En producción (anthropic u otro) invoca
    el prompt versionado.
    """
    llm = get_llm()
    if llm.name == "fake":
        doc_type = classify_document_type(text)
        return doc_type, "clasificación heurística (entorno de pruebas)"

    prompt = _load_prompt()
    snippet = text[:MAX_TEXT_CHARS]
    try:
        result = llm.complete(system=prompt, user=snippet)
        parsed = _extract_json(result.text)
        if parsed and parsed.get("document_type") in VALID_TYPES:
            return parsed["document_type"], parsed.get("reason", "")
        log.warning("LLM devolvió tipo inválido o JSON no parseable: %s", result.text[:200])
    except Exception:
        log.exception("falló clasificación con LLM; usando heurística")

    doc_type = classify_document_type(text)
    return doc_type, "fallback heurístico tras error del LLM"
