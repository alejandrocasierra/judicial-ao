"""Handler de reconstruccion del ledger de hechos."""
from __future__ import annotations

from typing import Any

from app.core.db import tx
from app.services import party_facts


def handle(job: dict[str, Any]) -> dict[str, Any]:
    if not job.get("organization_id") or not job.get("case_id"):
        return {"implemented": False, "pending_phase": "modulos-legales",
                "job_type": job.get("job_type"), "input_count": len(job.get("input_ids") or []),
                "note": "handler real requiere job persistido con organizacion"}
    case_id = str(job["case_id"])
    with tx(str(job["organization_id"]), str(job.get("created_by") or "")) as conn:
        result = party_facts.rebuild_case(
            conn, str(job["organization_id"]), case_id,
            str(job.get("created_by") or "") or None,
        )
        return {"implemented": True, "case_id": case_id, **result}
