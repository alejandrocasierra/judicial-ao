#!/usr/bin/env python3
"""Calibra ANSWER_MIN_GROUNDING_OVERLAP con el set de referencia.

Barre umbrales, calcula precision/recall/F1 de "claim soportado" contra las 50
etiquetas y elige el punto de equilibrio. El valor recomendado se imprime y se
puede fijar en el entorno.

Uso:
  python scripts/calibrate_grounding.py
  python scripts/calibrate_grounding.py --labels evals/judge/reference_labels.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", default=str(ROOT / "evals" / "judge" / "reference_labels.yaml"))
    p.add_argument("--out", default="var/grounding_calibration.json")
    args = p.parse_args()
    from app.services import answering

    data = yaml.safe_load(Path(args.labels).read_text(encoding="utf-8"))
    items = data.get("items") or []
    scored = []
    for item in items:
        ratio, missing = answering.grounding(str(item["claim"]), str(item["evidence"]))
        scored.append((ratio, bool(missing), int(item["human"])))
    rows = []
    best = None
    for step in range(10, 81):
        threshold = step / 100
        tp = fp = fn = tn = 0
        for ratio, missing, human in scored:
            pred = 1 if ratio >= threshold and not missing else 0
            if pred and human:
                tp += 1
            elif pred and not human:
                fp += 1
            elif not pred and human:
                fn += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        row = {"threshold": threshold, "precision": round(precision, 4),
               "recall": round(recall, 4), "f1": round(f1, 4),
               "tp": tp, "fp": fp, "fn": fn, "tn": tn}
        rows.append(row)
        if best is None or (row["f1"], -abs(threshold - 0.34)) > (best["f1"], -abs(best["threshold"] - 0.34)):
            best = row
    report = {"n": len(items), "best": best, "grid": rows}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"mejor_umbral={best['threshold']} f1={best['f1']} precision={best['precision']} recall={best['recall']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
