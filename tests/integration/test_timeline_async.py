"""IT-TLA — «Causas con IA» en segundo plano: encola un job `procedural_links`.

El pase IA puede tardar minutos y consumir tokens, así que no debe bloquear la
petición HTTP: el endpoint con `background=true` devuelve de inmediato y encola
un job del worker que corre link_events + review_events + propose_relations_llm.
"""
from __future__ import annotations

import pytest
from helpers import fetch

pytestmark = pytest.mark.integration


def test_it_tla_01_build_background_enqueues_procedural_links_job(client, auth, ids, owner_db):
    h = auth("admin.alfa")
    r = client.post(f"/v1/cases/{ids['pago']}/timeline/build?llm=true&background=true", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["background"] is True
    assert body["job_id"]
    job_type, status = fetch(owner_db, "SELECT job_type, status FROM jobs WHERE id = %s", (body["job_id"],))[0]
    assert job_type == "procedural_links"
    assert status in ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED")


def test_it_tla_02_build_background_requires_permission(client, auth, ids):
    h = auth("lector.alfa")  # READ_ONLY no tiene media.upload
    r = client.post(f"/v1/cases/{ids['pago']}/timeline/build?llm=true&background=true", headers=h)
    assert r.status_code == 403, r.text


def test_it_tla_03_build_background_dedups_within_window(client, auth, ids):
    h = auth("admin.alfa")
    a = client.post(f"/v1/cases/{ids['pago']}/timeline/build?llm=true&background=true", headers=h).json()
    b = client.post(f"/v1/cases/{ids['pago']}/timeline/build?llm=true&background=true", headers=h).json()
    assert a["job_id"] == b["job_id"]
    assert b["reused"] is True
