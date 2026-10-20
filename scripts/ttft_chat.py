#!/usr/bin/env python3
"""Mide TTFT (time to first stage) y tiempo al primer token validado por SSE.

Uso:
  EVAL_EMAIL=... EVAL_PASSWORD=... python scripts/ttft_chat.py --base-url http://127.0.0.1:8000 --case-id <uuid> --rounds 10
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p / 100
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--question", default="¿Qué evidencia hay sobre pagos?")
    ap.add_argument("--out", default="var/ttft.json")
    args = ap.parse_args()

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=120) as client:
        token = os.environ.get("EVAL_TOKEN")
        if not token:
            r = client.post("/v1/auth/login", json={"email": os.environ["EVAL_EMAIL"],
                                                    "password": os.environ["EVAL_PASSWORD"]})
            r.raise_for_status()
            token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        sid = args.session_id
        if not sid:
            r = client.post(f"/v1/cases/{args.case_id}/chats", headers=h, json={})
            r.raise_for_status()
            sid = r.json()["id"]

        ttft: list[float] = []
        first_token: list[float] = []
        for _ in range(max(1, args.rounds)):
            started = time.perf_counter()
            got_stage = False
            got_token = False
            with client.stream("POST", f"/v1/cases/{args.case_id}/chats/{sid}/messages/stream",
                               headers=h, json={"content": args.question, "strategy": "auto"}) as resp:
                resp.raise_for_status()
                event = ""
                for line in resp.iter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        elapsed = (time.perf_counter() - started) * 1000
                        if event == "stage" and not got_stage:
                            ttft.append(elapsed)
                            got_stage = True
                        if event == "token" and not got_token:
                            first_token.append(elapsed)
                            got_token = True
                        if event == "done":
                            break
    report = {
        "rounds": args.rounds,
        "ttft_stage_ms": {"p50": _pct(ttft, 50), "p95": _pct(ttft, 95)},
        "first_token_ms": {"p50": _pct(first_token, 50), "p95": _pct(first_token, 95)},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
