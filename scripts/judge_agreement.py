#!/usr/bin/env python3
"""Mide Cohen's κ entre el juez y las 50 etiquetas de referencia.

Uso:
  python scripts/judge_agreement.py
  python scripts/judge_agreement.py --labels evals/judge/reference_labels.yaml --min-kappa 0.6

El juez offline es heurístico (cifras/cobertura) y no llama a ningún proveedor.
Con `--llm live` usaría `eval_matrix.judge` con el proveedor configurado.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "api"))


def cohen_kappa(pred: list[int], human: list[int]) -> float:
    if len(pred) != len(human) or not pred:
        return 0.0
    n = len(pred)
    po = sum(1 for p, h in zip(pred, human, strict=False) if p == h) / n
    p_pred = sum(pred) / n
    p_human = sum(human) / n
    pe = p_pred * p_human + (1 - p_pred) * (1 - p_human)
    return 1.0 if pe == 1.0 and po == 1.0 else (po - pe) / (1 - pe)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", default=str(ROOT / "evals" / "judge" / "reference_labels.yaml"))
    p.add_argument("--min-kappa", type=float, default=0.6)
    p.add_argument("--llm", choices=["offline", "live"], default="offline")
    p.add_argument("--out", default="var/judge_agreement.json")
    args = p.parse_args()

    data = yaml.safe_load(Path(args.labels).read_text(encoding="utf-8"))
    items = data.get("items") or []
    from eval_matrix import judge

    pred: list[int] = []
    human: list[int] = []
    rows = []
    llm = None
    if args.llm == "live":
        from app.providers.llm import get_llm
        llm = get_llm()
    for item in items:
        result = judge(str(item["claim"]), str(item["evidence"]), llm, samples=1)
        vote = 1 if result.get("verdict") == "pass" else 0
        pred.append(vote)
        human.append(int(item["human"]))
        rows.append({"id": item.get("id"), "human": int(item["human"]), "judge": vote,
                     "faithfulness": result.get("faithfulness"), "mode": result.get("judge_mode")})
    kappa = cohen_kappa(pred, human)
    report = {"n": len(items), "kappa": round(kappa, 4), "min_kappa": args.min_kappa,
              "agreement": round(sum(1 for a, b in zip(pred, human, strict=False) if a == b) / max(1, len(items)), 4),
              "mode": args.llm, "rows": rows}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"n={report['n']} kappa={report['kappa']} agreement={report['agreement']} "
          f"min_kappa={args.min_kappa} -> {out}")
    return 0 if kappa >= args.min_kappa else 1


if __name__ == "__main__":
    raise SystemExit(main())
