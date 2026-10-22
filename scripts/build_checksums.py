"""Genera el manifiesto agregado del corpus legal (`checksums.json`).

El manifiesto es reproducible: `generated_at` se deriva del máximo
`metadata.retrieved_at` del corpus (o de la máxima `version_date`), nunca del
reloj de ejecución. Dos ejecuciones sobre el mismo corpus producen el mismo
archivo, byte a byte.

Uso:
  python scripts/build_checksums.py
  python scripts/build_checksums.py --check
  python scripts/build_checksums.py --now 2026-10-22T00:00:00+00:00
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "data" / "legal_rules" / "co"
MANIFEST_NAME = "checksums.json"
SCHEMA = "judicial-ai/legal-corpus-manifest/v1"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rules(directory: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        if path.name == MANIFEST_NAME:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("rules") or [data]
        if not isinstance(data, list):
            raise ValueError(f"{path}: se esperaba una lista o {{rules: []}}")
        for item in data:
            for field in ("source", "identifier"):
                if not item.get(field):
                    raise ValueError(f"{path}: falta {field}")
            text = item.get("text") or ""
            checksum = item.get("checksum") or ""
            if text and checksum and _sha256(text) != checksum:
                raise ValueError(f"{path}: checksum no coincide para {item['identifier']}")
            out.append({**item, "_path": path.relative_to(ROOT).as_posix()})
    return out


def _generated_at(rules: list[dict], override: str | None) -> str:
    if override:
        return override
    stamps = []
    for rule in rules:
        retrieved = str((rule.get("metadata") or {}).get("retrieved_at") or "")
        if retrieved:
            stamps.append(retrieved)
        elif rule.get("version_date"):
            stamps.append(f"{rule['version_date']}T00:00:00+00:00")
    return max(stamps) if stamps else "1970-01-01T00:00:00+00:00"


def build(directory: Path, now: str | None = None) -> dict:
    rules = _rules(directory)
    generated_at = _generated_at(rules, now)
    entries = []
    for rule in sorted(rules, key=lambda r: (r["_path"], str(r["source"]), str(r["identifier"]))):
        entries.append({
            "path": rule["_path"],
            "source": rule["source"],
            "identifier": str(rule["identifier"]),
            "version_date": rule.get("version_date"),
            "official_url": rule.get("official_url"),
            "checksum": rule.get("checksum"),
            "generated_at": generated_at,
        })
    by_source: dict[str, int] = {}
    for entry in entries:
        by_source[entry["source"]] = by_source.get(entry["source"], 0) + 1
    corpus_hash = _sha256(_canonical(entries))
    return {
        "schema": SCHEMA,
        "jurisdiction": "co",
        "generated_at": generated_at,
        "rules_count": len(entries),
        "sources_count": len(by_source),
        "sources": [{"source": name, "rules": count}
                    for name, count in sorted(by_source.items())],
        "corpus_sha256": corpus_hash,
        "rules": entries,
    }


def _write(manifest: dict, directory: Path) -> Path:
    target = directory / MANIFEST_NAME
    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    target.write_text(payload, encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Manifiesto agregado del corpus legal")
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    parser.add_argument("--now", help="Timestamp ISO-8601 fijo (rompe la reproducibilidad)")
    parser.add_argument("--check", action="store_true",
                        help="Verifica que el manifiesto existente esté al día")
    args = parser.parse_args()
    directory = Path(args.dir)
    manifest = build(directory, args.now)
    target = directory / MANIFEST_NAME
    if args.check:
        if not target.exists():
            raise SystemExit(f"Falta {target.relative_to(ROOT)}")
        current = json.loads(target.read_text(encoding="utf-8"))
        if _canonical(current) != _canonical(manifest):
            raise SystemExit(f"{target.relative_to(ROOT)} está desactualizado; "
                             "ejecuta scripts/build_checksums.py")
        print(f"OK: {target.relative_to(ROOT)} coincide ({manifest['rules_count']} normas)")
        return
    _write(manifest, directory)
    print(f"Escrito {target.relative_to(ROOT)}: {manifest['rules_count']} normas, "
          f"{manifest['sources_count']} fuentes, generated_at={manifest['generated_at']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — CLI con mensaje claro
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
