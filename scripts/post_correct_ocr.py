"""Post-corrección ortográfica del texto OCR ya extraído.

Usa SymSpell con un diccionario español para corregir palabras obviamente
mal reconocidas. No toca nombres propios si no tienen una corrección clara.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from symspellpy import SymSpell, Verbosity

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.db import tx  # noqa: E402

DEFAULT_DIC = ROOT / "data" / "es.dic"


def load_symspell(dic_path: Path) -> SymSpell:
    sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    # El formato .dic de Hunspell tiene la frecuencia en la segunda columna.
    # SymSpell puede leerlo directamente con count=0.
    sym.load_dictionary(str(dic_path), term_index=0, count_index=1, encoding="utf-8")
    return sym


def correct_text(sym: SymSpell, text: str) -> str:
    words = re.split(r"(\s+)", text)
    out: list[str] = []
    for w in words:
        if not w.strip() or not re.match(r"[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]+", w):
            out.append(w)
            continue
        suggestions = sym.lookup(w, Verbosity.CLOSEST, max_edit_distance=2)
        if suggestions and suggestions[0].term != w and suggestions[0].distance <= 1:
            out.append(suggestions[0].term)
        else:
            out.append(w)
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--dic", default=str(DEFAULT_DIC))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sym = load_symspell(Path(args.dic))
    with tx(args.org_id, args.user_id) as conn:
        from app.core.db import rows
        pages = rows(conn, "SELECT id, text FROM document_pages WHERE document_id IN "
                            "(SELECT id FROM documents WHERE case_id = :c)", c=args.case_id)
        print(f"Páginas a corregir: {len(pages)}")
        for p in pages:
            if not p["text"]:
                continue
            corrected = correct_text(sym, p["text"])
            if corrected != p["text"]:
                if args.dry_run:
                    print(f"[dry-run] page {p['id']}: {p['text'][:60]} -> {corrected[:60]}")
                else:
                    conn.execute("UPDATE document_pages SET text = :t WHERE id = :i",
                                 {"t": corrected, "i": p["id"]})
        if not args.dry_run:
            print("Cambios guardados.")


if __name__ == "__main__":
    main()
