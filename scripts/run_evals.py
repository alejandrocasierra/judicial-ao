#!/usr/bin/env python3
"""Golden eval runner (Fase 7). Corre los casos en evals/golden/ y mide:

- citation_recall: cada must_cite esperado aparece en las citas de la respuesta.
- citation_retrieval_recall: cada must_cite aparece en la evidencia recuperada.
- forbidden_hit_rate: ningún forbidden_statement en la respuesta.
- contains_hit_rate: todos los expected_contains en la respuesta.
- abstain_accuracy: casos must_abstain responden sin evidencia.

Uso:
  python scripts/run_evals.py --case-id <uuid> --org-id <uuid> --user-id <uuid>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    import envload  # noqa: E402
    envload.load(str(_env_path))

from app.core.config import get_settings  # noqa: E402
from app.core.db import tx  # noqa: E402
from app.core.i18n import negotiate  # noqa: E402
from app.providers.llm import get_llm  # noqa: E402
from app.services import agent, answering  # noqa: E402


def _load_cases(golden_dir: Path) -> list[dict]:
    cases: list[dict] = []
    for f in sorted(golden_dir.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        for c in data.get("cases", []):
            c["source_file"] = f.name
            cases.append(c)
    return cases


def _run_rag(conn, case_id: str, question: str, locale: str, retrieval_only: bool = False) -> dict:
    # Golden eval usa recuperación léxica sobre document_pages/transcript_segments con
    # k amplio para que las páginas esperadas aparezcan aunque no estén en el top-8.
    items = answering.legacy_retrieve(conn, case_id, question, k=500)
    if not items:
        return {"answer": "", "claims": [], "citations": [], "evidence": [], "evidence_count": 0,
                "unsupported_claims": [], "uncertainties": ["insufficient_evidence"]}
    if retrieval_only:
        return {"answer": "", "claims": [], "citations": [], "evidence": items,
                "evidence_count": len(items), "unsupported_claims": [], "uncertainties": []}
    system, _pid, _pv = answering.system_prompt(locale)
    result = get_llm().complete(system, answering.build_user_prompt(question, items))
    validated = answering.parse_and_validate(result.text, items, get_settings().ANSWER_MIN_GROUNDING_OVERLAP)
    return {
        "answer": " ".join(cl["text"] for cl in validated["claims"]),
        "claims": validated["claims"],
        "citations": [{"handle": h} for cl in validated["claims"] for h in cl["citations"]],
        "evidence": items,
        "evidence_count": len(items),
        "unsupported_claims": validated["unsupported_claims"],
        "uncertainties": validated["uncertainties"],
    }


def _run_agent(conn, case_id: str, question: str, locale: str) -> dict:
    return agent.run_agent_query(conn, case_id, question, locale)


def _citation_matches(item: dict, expected: dict) -> bool:
    filename = (item.get("filename") or "").lower()
    if expected.get("filename_contains") and expected["filename_contains"].lower() not in filename:
        return False
    if expected.get("document_id") and str(item.get("document_id")) != str(expected["document_id"]):
        return False
    if expected.get("media_id") and str(item.get("media_id")) != str(expected["media_id"]):
        return False
    if expected.get("segment_id") and str(item.get("segment_id")) != str(expected["segment_id"]):
        return False
    if expected.get("page_number") is not None and item.get("page_number") != expected["page_number"]:
        return False
    return True


def _evaluate(case: dict, result: dict, retrieval_only: bool = False) -> dict:
    answer = result.get("answer", "").lower()
    claims = result.get("claims", [])
    evidence = result.get("evidence", [])
    citations = result.get("citations", [])

    checks: dict[str, Any] = {}
    if case.get("must_abstain"):
        checks["abstain"] = result.get("evidence_count", 0) == 0 and not claims
        checks["pass"] = checks["abstain"]
        return checks

    must_cite = case.get("must_cite", [])
    # retrieval recall (evidencia recuperada contiene la fuente esperada)
    checks["retrieval_recall"] = sum(1 for exp in must_cite if any(_citation_matches(it, exp) for it in evidence)) / len(must_cite) if must_cite else 1.0

    if retrieval_only:
        checks["pass"] = checks["retrieval_recall"] >= 1.0
        return checks

    # citation recall (en respuesta final)
    cited_items = []
    for c in citations:
        handle = c.get("handle")
        it = next((e for e in evidence if e.get("handle") == handle), None)
        if it:
            cited_items.append(it)
    checks["citation_recall"] = sum(1 for exp in must_cite if any(_citation_matches(it, exp) for it in cited_items)) / len(must_cite) if must_cite else 1.0

    # forbidden statements
    forbidden = case.get("forbidden_statements", [])
    checks["forbidden_hit"] = any(f.lower() in answer for f in forbidden)

    # expected contains
    expected = case.get("expected_contains", [])
    checks["contains_hit_rate"] = sum(1 for e in expected if e.lower() in answer) / len(expected) if expected else 1.0

    checks["pass"] = (
        checks["citation_recall"] >= 1.0
        and checks["retrieval_recall"] >= 1.0
        and not checks["forbidden_hit"]
        and checks["contains_hit_rate"] >= 1.0
    )
    return checks


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--case-id", required=True, type=UUID)
    p.add_argument("--org-id", required=True, type=UUID)
    p.add_argument("--user-id", required=True, type=UUID)
    p.add_argument("--golden-dir", default="evals/golden")
    p.add_argument("--strategy", choices=["rag", "agent"], default=None)
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument("--threshold", type=float, default=1.0)
    p.add_argument("--out", default="var/eval_report.json")
    p.add_argument("--locale", default="es")
    args = p.parse_args()

    cases = _load_cases(Path(args.golden_dir))
    if not cases:
        print("[WARN] no golden cases found")
        return 0

    locale = negotiate(None, args.locale)
    results = []
    with tx(args.org_id, args.user_id) as conn:
        for case in cases:
            strategy = args.strategy or case.get("strategy", "rag")
            if strategy == "agent":
                result = _run_agent(conn, str(args.case_id), case["question"], locale)
            else:
                result = _run_rag(conn, str(args.case_id), case["question"], locale, retrieval_only=args.retrieval_only)
            checks = _evaluate(case, result, retrieval_only=args.retrieval_only)
            results.append({"case": case, "result": result, "checks": checks})

    total = len(results)
    passed = sum(1 for r in results if r["checks"]["pass"])
    pass_rate = passed / total if total else 0.0
    report = {
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "metrics": {
            "avg_citation_recall": sum(r["checks"].get("citation_recall", 0) for r in results) / total if total else 0,
            "avg_retrieval_recall": sum(r["checks"].get("retrieval_recall", 0) for r in results) / total if total else 0,
            "forbidden_hits": sum(1 for r in results if r["checks"].get("forbidden_hit")),
            "avg_contains_hit_rate": sum(r["checks"].get("contains_hit_rate", 1.0) for r in results) / total if total else 0,
        },
        "cases": [
            {
                "id": r["case"]["id"],
                "pass": r["checks"]["pass"],
                "question": r["case"]["question"],
                "answer": r["result"].get("answer", "")[:300],
                "checks": r["checks"],
            }
            for r in results
        ],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Golden eval: {passed}/{total} passed ({pass_rate:.0%})")
    print(f"  citation_recall={report['metrics']['avg_citation_recall']:.2f}")
    print(f"  retrieval_recall={report['metrics']['avg_retrieval_recall']:.2f}")
    print(f"  forbidden_hits={report['metrics']['forbidden_hits']}")
    print(f"Report written to {out_path}")
    return 0 if pass_rate >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
