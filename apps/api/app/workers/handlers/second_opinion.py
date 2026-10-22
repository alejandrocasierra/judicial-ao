"""Handler de la segunda opinion: ejecuta una corrida de `second_opinion_runs`."""
from __future__ import annotations

from typing import Any

from app.core.db import one, tx
from app.services import second_opinion


def handle(job: dict[str, Any]) -> dict[str, Any]:
    ids = job.get("input_ids") or []
    if not ids or not job.get("organization_id"):
        return {"implemented": False, "pending_phase": "modulos-legales",
                "job_type": job.get("job_type"), "input_count": len(ids),
                "note": "handler real requiere job persistido con organizacion"}
    run_id = str(ids[0])
    with tx(str(job["organization_id"]), str(job.get("created_by") or "")) as conn:
        run = one(conn, "SELECT * FROM second_opinion_runs WHERE id=:r", r=run_id)
        if not run:
            return {"implemented": True, "run_id": run_id, "status": "NOT_FOUND"}
        result = second_opinion.execute_run(conn, run_id)
        return {"implemented": True, **result}
