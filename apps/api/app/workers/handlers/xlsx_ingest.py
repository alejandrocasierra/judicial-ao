"""Handler del job `xlsx_ingest` (módulo Procesos): ingesta automática de índices XLSX."""
from __future__ import annotations

import logging
from typing import Any

from app.core.db import one, tx
from app.services.xlsx_ingest import ingest_xlsx

log = logging.getLogger(__name__)


def handle(job: dict[str, Any]) -> dict[str, Any]:
    org_id = str(job["organization_id"])
    case_id = str(job["case_id"])
    actor_id = str(job.get("created_by") or "00000000-0000-0000-0000-000000000000")
    input_ids = [str(x) for x in job.get("input_ids") or []]
    if not input_ids:
        return {"processed": 0, "errors": 0, "files": []}

    results: list[dict[str, Any]] = []
    errors = 0
    for source_id in input_ids:
        try:
            with tx(org_id, actor_id) as conn:
                case = one(conn, "SELECT case_number FROM cases WHERE id = :c", c=case_id)
                case_file = one(conn, """SELECT id, filename, storage_uri, folder_id FROM case_files
                                         WHERE id = :i AND case_id = :c""",
                                i=source_id, c=case_id)
                if case is None or case_file is None:
                    raise ValueError("case o case_file no encontrado")
                result = ingest_xlsx(conn, org_id=org_id, case_id=case_id, user_id=actor_id,
                                     case_file=case_file, case_number=case["case_number"])
                result["id"] = source_id
                result["status"] = "ok"
                results.append(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("xlsx_ingest falló para %s", source_id)
            errors += 1
            results.append({"id": source_id, "status": "error", "error": str(exc)[:300]})
    return {"processed": len(results), "errors": errors, "files": results}
