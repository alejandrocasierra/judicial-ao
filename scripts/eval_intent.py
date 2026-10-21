#!/usr/bin/env python3
"""Evalúa el router de intención contra el corpus etiquetado a mano.

Mide el router REAL (`app/services/intent.py`, solo reglas, sin LLM) y responde
las preguntas que importan antes de tocar nada:

- accuracy global y macro-F1
- precisión / recall / F1 por intención
- matriz de confusión completa
- **falsos positivos de `navegar` y `agregacion`**: son los caros (0 LLM, formato
  de salida distinto: una lista de menciones nunca responde una pregunta sustantiva)
- tasa de `default_lookup`: cuántas preguntas caen al cajón de `dato` porque
  ninguna regex las reconoció
- acierto por familia de regla (`reason`): señala qué regex está mintiendo
- accuracy sin las preguntas que exigen historial (`needs_history=si`)

Uso:

    python scripts/eval_intent.py --corpus evals/intent/corpus.csv
    python scripts/eval_intent.py --show-errors 40 --min-accuracy 0.75
    python scripts/eval_intent.py --out var/intent_eval.json --markdown

Las filas con `label` vacía se omiten (pendientes de etiquetar).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Callable

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

INTENTS = ("dato", "navegar", "analisis", "procedimental", "agregacion", "lectura", "correccion")

# Intenciones cuyo falso positivo cambia el FORMATO de la respuesta y gasta 0 LLM.
EXPENSIVE_FALSE_POSITIVES = ("navegar", "agregacion")

Prediction = tuple[str, float, str]  # (intent, confidence, reason)


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def load_corpus(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Devuelve (filas etiquetadas, filas omitidas). Falla si no hay corpus."""
    if not path.exists():
        raise SystemExit(f"[ERROR] no existe el corpus {path}. Genera uno con scripts/export_intent_corpus.py")
    with path.open("r", encoding="utf-8", newline="") as fh:
        raw = list(csv.DictReader(fh))
    labeled: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for row in raw:
        row = {k: (v or "") for k, v in row.items()}
        row["question"] = _clean(row.get("question"))
        label = _clean(row.get("label")).lower()
        if not row["question"] or label not in INTENTS:
            if row["question"]:
                skipped.append(row)
            continue
        row["label"] = label
        labeled.append(row)
    if not labeled:
        raise SystemExit(
            f"[ERROR] {path} no tiene filas etiquetadas. Rellena la columna `label` "
            f"con una de: {', '.join(INTENTS)}"
        )
    return labeled, skipped


def predict_rules(question: str, has_attachments: bool = False) -> Prediction:
    from app.services import intent

    decision = intent.classify(question, has_attachments=has_attachments)
    value = str(getattr(decision.intent, "value", decision.intent))
    return value, float(decision.confidence), str(decision.reason)


def confusion_matrix(rows: list[dict[str, str]]) -> dict[tuple[str, str], int]:
    matrix: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["label"], row["predicted"])
        matrix[key] = matrix.get(key, 0) + 1
    return matrix


def per_class(matrix: dict[tuple[str, str], int], labels: tuple[str, ...] = INTENTS) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = matrix.get((label, label), 0)
        fn = sum(n for (gold, pred), n in matrix.items() if gold == label and pred != label)
        fp = sum(n for (gold, pred), n in matrix.items() if pred == label and gold != label)
        support = tp + fn
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        out[label] = {
            "tp": float(tp),
            "fp": float(fp),
            "fn": float(fn),
            "support": float(support),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    return out


def macro_f1(per: dict[str, dict[str, float]]) -> float:
    """Media de F1 sobre las clases PRESENTES en el corpus (no diluye con las ausentes)."""
    present = [v["f1"] for v in per.values() if v["support"] > 0]
    return round(sum(present) / len(present), 4) if present else 0.0


def accuracy(rows: list[dict[str, str]]) -> float:
    return round(sum(1 for r in rows if r["label"] == r["predicted"]) / len(rows), 4) if rows else 0.0


def _by_reason(rows: list[dict[str, str]]) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault(row.get("reason") or "sin_reason", []).append(row)
    out: dict[str, dict[str, float]] = {}
    for reason, items in sorted(groups.items()):
        correct = sum(1 for r in items if r["label"] == r["predicted"])
        out[reason] = {
            "n": float(len(items)),
            "accuracy": round(correct / len(items), 4),
        }
    return out


def _critical(per: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for intent in EXPENSIVE_FALSE_POSITIVES:
        data = per[intent]
        predicted = data["tp"] + data["fp"]
        out[intent] = {
            "fp": data["fp"],
            "fp_rate": round(data["fp"] / predicted, 4) if predicted else 0.0,
            "fn": data["fn"],
        }
    return out


def _by_source(rows: list[dict[str, str]]) -> dict[str, dict[str, float]]:
    """Accuracy por procedencia: lo real pesa distinto que la semilla escrita por un agente."""
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault(row.get("source") or "seed", []).append(row)
    out: dict[str, dict[str, float]] = {}
    for source, items in sorted(groups.items()):
        correct = sum(1 for r in items if r["label"] == r["predicted"])
        out[source] = {"n": float(len(items)), "accuracy": round(correct / len(items), 4)}
    return out


def evaluate(rows: list[dict[str, str]]) -> dict[str, Any]:
    matrix = confusion_matrix(rows)
    per = per_class(matrix)
    no_history = [r for r in rows if _clean(r.get("needs_history")).lower() not in ("si", "sí", "true", "1")]
    default_rows = [r for r in rows if r.get("reason") == "default_lookup"]
    low_conf = [r for r in rows if float(r.get("confidence") or 1.0) < 0.5]
    return {
        "cases": len(rows),
        "accuracy": accuracy(rows),
        "macro_f1": macro_f1(per),
        "per_class": per,
        "confusion": {f"{gold}->{pred}": n for (gold, pred), n in sorted(matrix.items())},
        "critical": _critical(per),
        "default_rate": round(len(default_rows) / len(rows), 4) if rows else 0.0,
        "low_confidence_rate": round(len(low_conf) / len(rows), 4) if rows else 0.0,
        "by_reason": _by_reason(rows),
        "by_source": _by_source(rows),
        "no_history_cases": len(no_history),
        "accuracy_no_history": accuracy(no_history),
    }


def _fmt(value: Any, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}" if pct else f"{value}"


def _markdown(report: dict[str, Any], corpus: Path, errors: list[dict[str, str]]) -> str:
    lines = [
        "# Router de intención — baseline por reglas",
        "",
        f"- Corpus: `{corpus}` · **{report['cases']}** preguntas etiquetadas",
        f"- Accuracy: **{report['accuracy']:.1%}** · macro-F1: **{report['macro_f1']:.3f}**",
        f"- `default_lookup`: **{report['default_rate']:.1%}** de los turnos no los reconoce ninguna regla",
        f"- Confianza &lt; 0.5: **{report['low_confidence_rate']:.1%}** (solo ahí se consultaría un LLM)",
        f"- Accuracy sin preguntas que exigen historial: **{report['accuracy_no_history']:.1%}** "
        f"({report['no_history_cases']} casos)",
        "",
        "## Por intención",
        "",
        "| Intención | Soporte | Precision | Recall | F1 |",
        "|---|---|---|---|---|",
    ]
    for intent, data in report["per_class"].items():
        lines.append(
            f"| `{intent}` | {int(data['support'])} | {data['precision']:.1%} | "
            f"{data['recall']:.1%} | {data['f1']:.3f} |"
        )
    lines += ["", "## Errores caros (formato equivocado, 0 LLM)", "",
              "| Intención | Falsos positivos | FP rate | No detectadas (FN) |",
              "|---|---|---|---|"]
    for intent, data in report["critical"].items():
        lines.append(f"| `{intent}` | {int(data['fp'])} | {data['fp_rate']:.1%} | {int(data['fn'])} |")
    lines += ["", "## Acierto por procedencia", "", "| Origen | Casos | Accuracy |", "|---|---|---|"]
    for source, data in report.get("by_source", {}).items():
        lines.append(f"| `{source}` | {int(data['n'])} | {data['accuracy']:.1%} |")
    lines += ["", "## Acierto por familia de regla", "", "| reason | Casos | Accuracy |", "|---|---|---|"]
    for reason, data in report["by_reason"].items():
        lines.append(f"| `{reason}` | {int(data['n'])} | {data['accuracy']:.1%} |")
    if errors:
        lines += ["", "## Errores (esperado → predicho)", ""]
        for row in errors:
            lines.append(
                f"- **{row['label']} → {row['predicted']}** (`{row['reason']}`, {row['confidence']}): "
                f"{row['question']}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Evalúa el router de intención (reglas) contra el corpus etiquetado.")
    p.add_argument("--corpus", default="evals/intent/corpus.csv")
    p.add_argument("--out", default="var/intent_eval.json")
    p.add_argument("--show-errors", type=int, default=25, help="Errores a imprimir (0 = ninguno)")
    p.add_argument("--min-accuracy", type=float, default=None, help="Sale con código 1 si no se alcanza")
    args = p.parse_args()

    corpus = Path(args.corpus)
    if not corpus.is_absolute():
        corpus = _ROOT / corpus
    rows, skipped = load_corpus(corpus)

    predict: Callable[[str, bool], Prediction] = predict_rules
    for row in rows:
        has_attachments = _clean(row.get("has_attachments")).lower() in ("si", "sí", "true", "1")
        predicted, confidence, reason = predict(row["question"], has_attachments)
        row["predicted"] = predicted
        row["confidence"] = f"{confidence:.2f}"
        row["reason"] = reason

    report = evaluate(rows)
    report["skipped_unlabeled"] = len(skipped)
    report["corpus"] = str(corpus)

    errors = [
        {"question": r["question"], "label": r["label"], "predicted": r["predicted"],
         "confidence": r["confidence"], "reason": r["reason"]}
        for r in rows if r["label"] != r["predicted"]
    ]
    report["errors"] = errors

    out = Path(args.out)
    if not out.is_absolute():
        out = _ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = out.with_suffix(".md")
    md_path.write_text(_markdown(report, corpus, errors[: args.show_errors]), encoding="utf-8")

    print(f"Intención (reglas): {report['cases']} casos · accuracy {report['accuracy']:.1%} · macro-F1 {report['macro_f1']:.3f}")
    if skipped:
        print(f"  omitidas sin etiqueta: {len(skipped)}")
    print(f"  default_lookup={report['default_rate']:.1%}  confianza<0.5={report['low_confidence_rate']:.1%}")
    for intent, data in report["critical"].items():
        print(f"  FP {intent}: {int(data['fp'])} ({data['fp_rate']:.1%} de lo predicho como {intent})")
    print("  por intención:", ", ".join(
        f"{k}={v['f1']:.2f}" for k, v in report["per_class"].items() if v["support"] > 0))
    if args.show_errors and errors:
        print(f"\n  Errores ({min(len(errors), args.show_errors)} de {len(errors)}):")
        for row in errors[: args.show_errors]:
            print(f"   - [{row['label']} → {row['predicted']}] {row['question']}  ({row['reason']})")
    print(f"\nReporte: {out} (+ {md_path})")
    if args.min_accuracy is not None and report["accuracy"] < args.min_accuracy:
        print(f"[FAIL] accuracy {report['accuracy']:.1%} < mínimo {args.min_accuracy:.1%}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
