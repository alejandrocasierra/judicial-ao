#!/usr/bin/env python3
"""Genera el set de 50 etiquetas de referencia para medir acuerdo del juez.

No finge anotación humana: son etiquetas **de referencia** construidas a partir
del set dorado (claim = texto esperado; contraejemplo = claim con una cifra
inventada). El runner `judge_agreement.py` mide Cohen's κ contra ellas y falla
si κ < 0.6. Cuando haya anotadores humanos, se reemplaza este archivo por el
suyo manteniendo el mismo esquema.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "judge" / "reference_labels.yaml"


def main() -> int:
    golden = yaml.safe_load((ROOT / "evals" / "golden" / "chat_pipeline.yaml").read_text(encoding="utf-8"))
    cases = golden.get("cases") or []
    items = []
    for index, case in enumerate(cases[:50]):
        evidence = " ".join(case.get("must_contain") or []) or case["question"]
        if index % 2 == 0:
            claim = evidence
            human = 1
            note = "claim contenido en la evidencia"
        else:
            claim = f"{evidence} El monto fue $99.999.999."
            human = 0
            note = "cifra inventada no presente en la evidencia"
        items.append({"id": case["id"], "claim": claim, "evidence": evidence,
                      "human": human, "note": note})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(yaml.safe_dump({"version": 1, "items": items}, allow_unicode=True,
                                  sort_keys=False), encoding="utf-8")
    print(f"escritas {len(items)} etiquetas en {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
