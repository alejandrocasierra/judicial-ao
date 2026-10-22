"""Runner autocontenido del golden set legal (`legal_modules.yaml`).

No usa LLM: ejecuta el gate determinista real y verifica la segunda opinión
persistida. Así el resultado es reproducible y no cuesta tokens.

Uso:
  python evals/golden/legal_modules_runner.py
  python evals/golden/legal_modules_runner.py --yaml evals/golden/legal_modules.yaml --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
load_dotenv(ROOT / ".env")

from app.services import legal_guard  # noqa: E402


def _engine():
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_SUPERUSER']}:"
        f"{quote_plus(os.environ['POSTGRES_SUPERUSER_PASSWORD'])}@"
        f"{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/"
        f"{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


def _citation(conn, org_id: str, spec: dict) -> dict:
    row = conn.execute(text("""
        SELECT text FROM document_pages
        WHERE organization_id = :o AND document_id = :d AND page_number = :p
    """), {"o": org_id, "d": spec["document_id"], "p": spec["page_number"]}).first()
    if not row or not row[0]:
        raise RuntimeError(
            f"No existe la página {spec['page_number']} del documento "
            f"{spec['document_id']} en la organización {org_id}."
        )
    return {
        "handle": "E1",
        "source_type": "document_page",
        "document_id": spec["document_id"],
        "page_number": spec["page_number"],
        "text": row[0],
        "filename": f"documento-{spec['document_id'][:8]}.pdf",
    }


def _check_legal_guard(conn, case: dict, fixtures: dict) -> dict:
    citation_key = case["citation"]
    spec = fixtures[citation_key]
    item = _citation(conn, fixtures["organization_id"], spec)
    result = legal_guard.audit(
        conn,
        org_id=fixtures["organization_id"],
        case_id=fixtures["case_id"],
        question=case["claim"],
        answer=case["claim"],
        claims=[{"text": case["claim"], "citations": ["E1"]}],
        items=[item],
        policy=legal_guard.default_policy(),
    )
    claim = result["claims"][0]
    reasons = {str(r.get("code")) for r in claim.get("reasons") or []}
    problems: list[str] = []
    if claim["status"] != case["expected_status"]:
        problems.append(f"status={claim['status']} esperado={case['expected_status']}")
    for code in case.get("expected_reasons_include") or []:
        if code not in reasons:
            problems.append(f"falta razón {code}")
    for code in case.get("expected_reasons_exclude") or []:
        if code in reasons:
            problems.append(f"razón inesperada {code}")
    return {
        "id": case["id"],
        "name": case["name"],
        "kind": "legal_guard",
        "ok": not problems,
        "status": claim["status"],
        "reasons": sorted(reasons),
        "problems": problems,
    }


def _check_second_opinion(conn, case: dict, fixtures: dict) -> dict:
    run_id = fixtures[case["run_id"]]
    row = conn.execute(text("""
        SELECT status, verdict, verifier_model_name, tokens_in,
               responder_model_run_id
        FROM second_opinion_runs WHERE id = :id
    """), {"id": run_id}).first()
    if not row:
        return {"id": case["id"], "name": case["name"], "kind": "second_opinion",
                "ok": False, "problems": [f"no existe el run {run_id}"]}
    status, verdict, verifier_name, tokens_in, responder_run_id = row
    responder = conn.execute(text("""
        SELECT provider, model FROM model_runs WHERE id = :id
    """), {"id": responder_run_id}).first()
    claims = conn.execute(text("""
        SELECT count(*) FROM second_opinion_claims WHERE run_id = :id
    """), {"id": run_id}).scalar_one()
    responder_name = f"{responder[0]}/{responder[1]}" if responder else ""
    problems: list[str] = []
    if status != case["expected_status"]:
        problems.append(f"status={status} esperado={case['expected_status']}")
    if case.get("expected_verdict") and verdict != case["expected_verdict"]:
        problems.append(f"verdict={verdict} esperado={case['expected_verdict']}")
    if case.get("require_distinct_verifier") and responder_name:
        if verifier_name.lower() in {responder_name.lower(), str(responder[1]).lower()}:
            problems.append(f"verificador no distinto ({verifier_name})")
    if tokens_in is None or tokens_in < int(case.get("expected_min_tokens_in") or 0):
        problems.append(f"tokens_in={tokens_in}")
    if claims < int(case.get("expected_min_claims") or 0):
        problems.append(f"claims={claims}")
    return {
        "id": case["id"],
        "name": case["name"],
        "kind": "second_opinion",
        "ok": not problems,
        "status": status,
        "verdict": verdict,
        "verifier": verifier_name,
        "responder": responder_name,
        "tokens_in": tokens_in,
        "claims": claims,
        "problems": problems,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Golden set legal determinista")
    parser.add_argument("--yaml", default=str(ROOT / "evals" / "golden" / "legal_modules.yaml"))
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    args = parser.parse_args()
    data = yaml.safe_load(Path(args.yaml).read_text(encoding="utf-8"))
    fixtures = data["fixtures"]
    engine = _engine()
    results = []
    with engine.connect() as conn:
        for case in data["cases"]:
            if case["kind"] == "legal_guard":
                results.append(_check_legal_guard(conn, case, fixtures))
            elif case["kind"] == "second_opinion":
                results.append(_check_second_opinion(conn, case, fixtures))
            else:
                results.append({"id": case.get("id"), "name": case.get("name"),
                                "ok": False, "problems": [f"kind desconocido {case.get('kind')}"]})
    passed = sum(1 for r in results if r["ok"])
    if args.json:
        print(json.dumps({"passed": passed, "total": len(results), "results": results},
                         ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "PASS" if r["ok"] else "FAIL"
            detail = f"status={r.get('status')}"
            if r.get("reasons"):
                detail += f" reasons={','.join(r['reasons'])}"
            if r.get("verdict"):
                detail += f" verdict={r['verdict']} verifier={r.get('verifier')}"
            print(f"[{mark}] {r['id']} {r['name']} ({detail})")
            for problem in r.get("problems") or []:
                print(f"       - {problem}")
        print(f"\n{passed}/{len(results)} casos PASS")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
