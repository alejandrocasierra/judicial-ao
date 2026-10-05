"""Abstracción de jurisdicción (SSD §66-67). Reglas en config/jurisdictions/*.yaml."""
from __future__ import annotations

import re
from functools import lru_cache

import yaml

from app.core.config import get_settings


@lru_cache
def jurisdictions() -> dict[str, dict]:
    s = get_settings()
    out = {}
    for f in sorted(s.path(s.JURISDICTIONS_DIR).glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        out[d["code"]] = d
    return out


def validate_case_number(jurisdiction: str, case_number: str) -> bool:
    j = jurisdictions().get(jurisdiction)
    return bool(j) and re.fullmatch(j["case_number_pattern"].strip("^$"), case_number) is not None


def normalize_citations(jurisdiction: str, text: str) -> list[dict]:
    """'Art. 1602 C.C.' | 'Artículo 1602 del Código Civil' | 'C.C., art. 1602' -> forma canónica."""
    j = jurisdictions().get(jurisdiction) or {}
    found: dict[tuple, dict] = {}
    for rule in j.get("citation_rules", []):
        for m in re.finditer(rule["pattern"], text):
            key = (rule["code"], m.group(1))
            found[key] = {"jurisdiction": jurisdiction, "code": rule["code"], "article": m.group(1), "version": None}
    return list(found.values())
