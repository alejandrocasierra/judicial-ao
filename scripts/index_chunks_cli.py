#!/usr/bin/env python3
"""Indexa chunks para búsqueda híbrida (FTS + vector).

Uso:

    python scripts/index_chunks_cli.py --case-id <uuid> --org-id <uuid> [--user-id <uuid>]
    python scripts/index_chunks_cli.py --document-id <uuid> --case-id <uuid> --org-id <uuid>
    python scripts/index_chunks_cli.py --media-id <uuid> --case-id <uuid> --org-id <uuid>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    import envload  # noqa: E402

    envload.load(str(_env_path))

from app.core.db import tx  # noqa: E402
from app.services import indexing  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Indexa chunks para búsqueda híbrida")
    p.add_argument("--case-id", required=True, type=UUID)
    p.add_argument("--org-id", required=True, type=UUID)
    p.add_argument("--user-id", type=UUID, default=None)
    p.add_argument("--document-id", type=UUID, default=None)
    p.add_argument("--media-id", type=UUID, default=None)
    args = p.parse_args()

    with tx(args.org_id, args.user_id) as conn:
        if args.document_id:
            result = indexing.index_document(conn, str(args.org_id), str(args.case_id), str(args.document_id), str(args.user_id))
        elif args.media_id:
            result = indexing.index_media(conn, str(args.org_id), str(args.case_id), str(args.media_id), str(args.user_id))
        else:
            result = indexing.index_case(conn, str(args.org_id), str(args.case_id), str(args.user_id))
    print(f"[OK] {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
