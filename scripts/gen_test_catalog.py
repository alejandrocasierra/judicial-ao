#!/usr/bin/env python3
"""Genera docs/TEST_CATALOG.md a partir del código de pruebas (ID, suite, archivo, descripción).
Así el catálogo nunca se desincroniza. Uso: python3 scripts/gen_test_catalog.py"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORDER = ["unit", "integration", "security", "behavior", "static"]
TITLES = {"unit": "Unitarias", "integration": "Integración (API + PostgreSQL)", "security": "Seguridad",
          "behavior": "Comportamiento: DEBE / NO DEBE", "static": "Análisis estático"}


def main() -> None:
    out = ["# Catálogo de pruebas (generado)", "",
           "> Archivo generado por `scripts/gen_test_catalog.py`. No editar a mano.", ""]
    total = 0
    for suite in ORDER:
        rows = []
        for f in sorted((ROOT / "tests" / suite).glob("test_*.py")):
            mod = ast.parse(f.read_text(encoding="utf-8"))
            mdoc = (ast.get_docstring(mod) or "").split("\n")[0]
            for n in mod.body:
                if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"):
                    doc = (ast.get_docstring(n) or "").split("\n")[0]
                    params = any(isinstance(d, ast.Call) and "parametrize" in ast.unparse(d.func) for d in n.decorator_list)
                    rows.append((n.name, f.name, doc or mdoc, "sí" if params else ""))
        total += len(rows)
        out += [f"## {TITLES[suite]} — {len(rows)} funciones", "", "| Prueba | Archivo | Qué verifica | Parametrizada |",
                "|---|---|---|---|"]
        out += [f"| `{a}` | {b} | {c.replace('|', '/')} | {d} |" for a, b, c, d in rows]
        out.append("")
    out.insert(4, f"Total de funciones de prueba: **{total}** (los casos parametrizados multiplican las ejecuciones).\n")
    (ROOT / "docs" / "TEST_CATALOG.md").write_text("\n".join(out), encoding="utf-8")
    print(f"[catalog] {total} funciones -> docs/TEST_CATALOG.md")


if __name__ == "__main__":
    main()
