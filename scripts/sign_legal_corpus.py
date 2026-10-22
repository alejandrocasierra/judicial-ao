"""Registra la aceptación jurídica del corpus con identidad y hash.

No inventa una firma: exige nombre, licencia profesional y confirmación
explícita. El resultado es una atestación auditable; si la organización usa
firma electrónica certificada, debe conservarse también el documento externo.

Uso:
  python scripts/sign_legal_corpus.py --signer "Nombre" --license "TP-123" --dry-run
  python scripts/sign_legal_corpus.py --signer "Nombre" --license "TP-123" --confirm
  python scripts/sign_legal_corpus.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "data" / "legal_rules" / "co"
MANIFEST = CORPUS_DIR / "checksums.json"
SIGNOFF = CORPUS_DIR / "signoff.json"
SCHEMA = "judicial-ai/legal-corpus-signoff/v1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        raise SystemExit(f"No existe {MANIFEST}. Ejecuta scripts/build_checksums.py.")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if data.get("schema") != "judicial-ai/legal-corpus-manifest/v1":
        raise SystemExit("El manifiesto no tiene el schema esperado.")
    if not data.get("rules") or not data.get("corpus_sha256"):
        raise SystemExit("El manifiesto está incompleto.")
    return data


def _payload(manifest: dict, signer: str, license_id: str, notes: str) -> dict:
    payload = {
        "schema": SCHEMA,
        "jurisdiction": manifest.get("jurisdiction") or "co",
        "rules_count": manifest["rules_count"],
        "sources_count": manifest["sources_count"],
        "corpus_sha256": manifest["corpus_sha256"],
        "checksums_sha256": _sha256_bytes(MANIFEST.read_bytes()),
        "signer": signer,
        "professional_license": license_id,
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "signature_kind": "ATTESTATION_SHA256",
        "scope": "Corpus legal cargado; revisión jurídica humana de los artículos y fuentes.",
        "notes": notes,
    }
    payload["signature_sha256"] = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Firma/atestigua el corpus legal cargado.")
    parser.add_argument("--signer", help="Nombre completo del revisor jurídico")
    parser.add_argument("--license", dest="license_id", help="Tarjeta profesional o identificador")
    parser.add_argument("--notes", default="", help="Alcance o salvedades de la revisión")
    parser.add_argument("--confirm", action="store_true", help="Escribe la atestación")
    parser.add_argument("--dry-run", action="store_true", help="Muestra el payload sin escribir")
    parser.add_argument("--verify", action="store_true", help="Verifica una atestación existente")
    args = parser.parse_args()

    manifest = _load_manifest()
    if args.verify:
        if not SIGNOFF.exists():
            print("No hay atestación registrada.")
            return 1
        signed = json.loads(SIGNOFF.read_text(encoding="utf-8"))
        expected = signed.pop("signature_sha256", "")
        actual = hashlib.sha256(_canonical(signed).encode("utf-8")).hexdigest()
        ok = expected == actual and signed.get("corpus_sha256") == manifest.get("corpus_sha256")
        print(f"{'OK' if ok else 'MISMATCH'}: {SIGNOFF}")
        return 0 if ok else 1

    if not args.signer or not args.license_id:
        raise SystemExit("Faltan --signer y --license. No se puede anonimizar una firma jurídica.")
    payload = _payload(manifest, args.signer.strip(), args.license_id.strip(), args.notes.strip())
    if args.dry_run or not args.confirm:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("Dry-run: no se escribió la atestación. Usa --confirm para registrarla.")
        return 0
    SIGNOFF.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Atestación escrita: {SIGNOFF}")
    print(f"corpus_sha256={payload['corpus_sha256']}")
    print(f"signature_sha256={payload['signature_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
