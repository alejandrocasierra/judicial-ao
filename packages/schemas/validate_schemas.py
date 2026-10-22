"""Validacion reproducible de los JSON Schema de packages/schemas.

Uso (desde la raiz del repo):

    .venv/Scripts/python.exe packages/schemas/validate_schemas.py

Comprueba que cada schema parsea y cumple su metaschema (Draft 2020-12 o
Draft-07). Ademas valida casos de uso reales de los tres schemas nuevos de los
modulos legales, incluidos los casos que DEBEN fallar: un contrato que acepta
todo no protege nada.

Sin dependencias del proyecto: solo `jsonschema` (ya requerido por la API).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, Draft7Validator

SCHEMAS = Path(__file__).resolve().parent
failures: list[str] = []


def load(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def validator_for(schema: dict):
    if schema.get("$schema", "").endswith("draft-07/schema#"):
        return Draft7Validator
    return Draft202012Validator


def check(schema_name: str, instance: dict, should_pass: bool, label: str) -> None:
    schema = load(schema_name)
    cls = validator_for(schema)
    cls.check_schema(schema)
    errors = sorted(cls(schema).iter_errors(instance), key=lambda e: list(e.path))
    ok = (not errors) == should_pass
    print(f"{'OK  ' if ok else 'FAIL'}  {label}: {'valid' if not errors else 'invalid'} ({len(errors)} error(s))")
    if not ok:
        failures.append(label)
        for error in errors[:3]:
            print("       -", error.message)


def metaschemas() -> None:
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            validator_for(schema).check_schema(schema)
            print(f"OK    metaschema {path.name}")
        except Exception as exc:  # noqa: BLE001 - el objetivo es reportar, no clasificar
            failures.append(f"metaschema {path.name}")
            print(f"FAIL  metaschema {path.name}: {exc}")


AUDIT = {
    "id": "11111111-1111-1111-1111-111111111111",
    "organization_id": "22222222-2222-2222-2222-222222222222",
    "case_id": "33333333-3333-3333-3333-333333333333",
    "status": "WARNING",
    "mode": "BALANCED",
    "claims_total": 1,
    "claims_supported": 1,
    "claims_blocked": 0,
    "findings_critical": 0,
    "claims": [{
        "claim_index": 0,
        "claim_text": "El demandado recibio el pago el 1 de febrero de 2019.",
        "claim_hash": "a" * 64,
        "claim_type": "FACT",
        "status": "SUPPORTED",
        "auditor": "DETERMINISTIC",
        "citation_handles": ["E1"],
    }],
    "citations": [{
        "audit_id": "11111111-1111-1111-1111-111111111111",
        "valid": True,
        "reasons": [],
        "page_number": 12,
        "quote_hash": "b" * 64,
    }],
    "findings": [{
        "audit_id": "11111111-1111-1111-1111-111111111111",
        "code": "UNSUPPORTED_INFERENCE",
        "severity": "HIGH",
        "message": "El claim infiere intencion no contenida en la fuente.",
        "blocking": False,
    }],
}

SECOND_OPINION = {
    "id": "44444444-4444-4444-4444-444444444444",
    "organization_id": "22222222-2222-2222-2222-222222222222",
    "case_id": "33333333-3333-3333-3333-333333333333",
    "status": "COMPLETED",
    "verdict": "PARTIAL",
    "verifier_model_name": "modelo-verificador",
    "responder_model_run_id": "55555555-5555-5555-5555-555555555555",
    "tokens_in": 9095,
    "tokens_out": 444,
    "cost": 0.005213,
    "claims": [{
        "run_id": "44444444-4444-4444-4444-444444444444",
        "claim_index": 0,
        "claim_text": "El pago consta en el comprobante.",
        "claim_hash": "c" * 64,
        "status": "SUPPORTED",
        "comparison": "MATCH",
    }],
    "findings": [],
}

FACT = {
    "id": "66666666-6666-6666-6666-666666666666",
    "organization_id": "22222222-2222-2222-2222-222222222222",
    "case_id": "33333333-3333-3333-3333-333333333333",
    "proposition": "El contrato se firmo el 1 de febrero de 2019.",
    "proof_status": "ALLEGED",
    "proof_basis": "NONE",
    "is_proven": False,
    "version": 1,
    "positions": [{"side": "DEMANDANTE", "position": "ALLEGES"}],
}

PROOF_REVIEW = {
    "fact_id": "66666666-6666-6666-6666-666666666666",
    "recommendation": "NEEDS_REVIEW",
    "proof_status_suggested": "EVIDENCE_SUPPORTED",
    "reasons": ["Hay evidencia que soporta el hecho, pero falta decision o admision."],
    "citation_handles": ["E1"],
    "requires_human": True,
}


def contracts() -> None:
    audit_bad = {**AUDIT, "status": "PASSED", "findings_critical": 2}
    check("legal_audit.schema.json", AUDIT, True, "legal_audit valido")
    check("legal_audit.schema.json", audit_bad, False, "legal_audit PASSED con criticos")

    so_without_verdict = {**SECOND_OPINION, "verdict": None}
    so_failed = {**SECOND_OPINION, "status": "FAILED", "verdict": None}
    check("second_opinion.schema.json", SECOND_OPINION, True, "second_opinion valido")
    check("second_opinion.schema.json", so_without_verdict, False, "second_opinion COMPLETED sin verdict")
    check("second_opinion.schema.json", so_failed, False, "second_opinion FAILED sin error_code")

    fact_claimed_proven = {**FACT, "is_proven": True}
    fact_admitted_without_position = {
        **FACT, "proof_status": "ADMITTED", "proof_basis": "ADMISSION", "is_proven": True,
    }
    fact_judicial_without_decision = {
        **FACT, "proof_status": "JUDICIALLY_PROVEN", "proof_basis": "JUDICIAL_DECISION", "is_proven": True,
    }
    check("party_fact.schema.json", FACT, True, "party_fact valido")
    check("party_fact.schema.json", fact_claimed_proven, False, "party_fact ALLEGED con is_proven")
    check("party_fact.schema.json", fact_admitted_without_position, False, "party_fact ADMITTED sin posicion ADMITS")
    check("party_fact.schema.json", fact_judicial_without_decision, False, "party_fact JUDICIALLY_PROVEN sin decision")

    review_schema = load("party_fact.schema.json")["$defs"]["proof_review"]
    Draft202012Validator(review_schema).validate(PROOF_REVIEW)
    print("OK    proof_review valido")
    forbidden = {**PROOF_REVIEW, "proof_status_suggested": "JUDICIALLY_PROVEN"}
    try:
        Draft202012Validator(review_schema).validate(forbidden)
    except Exception:
        print("OK    proof_review con JUDICIALLY_PROVEN rechazado")
    else:
        failures.append("proof_review con JUDICIALLY_PROVEN")
        print("FAIL  proof_review con JUDICIALLY_PROVEN (debe fallar)")


def main() -> int:
    metaschemas()
    contracts()
    print()
    print("RESULT:", "PASS" if not failures else f"FAIL ({len(failures)}): {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
