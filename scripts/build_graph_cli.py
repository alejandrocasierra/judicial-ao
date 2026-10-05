#!/usr/bin/env python3
"""Reconstruye el knowledge graph de un caso (CLI admin)."""
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
from app.services import graph  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Reconstruye el knowledge graph de un caso")
    p.add_argument("--case-id", required=True, type=UUID)
    p.add_argument("--org-id", required=True, type=UUID)
    p.add_argument("--user-id", type=UUID, default=None)
    args = p.parse_args()

    with tx(args.org_id, args.user_id) as conn:
        result = graph.build_case_graph(conn, str(args.org_id), str(args.case_id), str(args.user_id))
    print(f"[OK] {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
