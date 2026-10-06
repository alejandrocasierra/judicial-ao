"""IT-SRCH — buscador del proceso (carpetas, archivos, documentos, videos) y
solicitud de eliminación de media (videos), con su aislamiento entre casos."""
from __future__ import annotations

import pytest
from helpers import fetch

pytestmark = pytest.mark.integration


def test_it_srch_01_search_finds_folders_across_subfolders(client, auth, ids):
    h = auth("admin.alfa")
    pago = ids["pago"]
    parent = client.post(f"/v1/cases/{pago}/folders", headers=h, json={"name": "ZZBuscarPadre"}).json()
    client.post(f"/v1/cases/{pago}/folders", headers=h,
                json={"name": "ZZBuscarHija", "parent_id": parent["id"]})
    r = client.get(f"/v1/cases/{pago}/search", headers=h, params={"q": "ZZBuscar"})
    assert r.status_code == 200, r.text
    body = r.json()
    names = {f["name"] for f in body["folders"]}
    assert {"ZZBuscarPadre", "ZZBuscarHija"} <= names
    hija = next(f for f in body["folders"] if f["name"] == "ZZBuscarHija")
    assert hija["path"].startswith("ZZBuscarPadre")  # incluye la ruta padre


def test_it_srch_02_search_finds_documents_by_name(client, auth, ids):
    h = auth("abogada.alfa")
    r = client.get(f"/v1/cases/{ids['pago']}/search", headers=h, params={"q": "demanda"})
    assert r.status_code == 200, r.text
    body = r.json()
    filenames = {it["filename"] for it in body["items"]}
    assert any("demanda" in fn.lower() for fn in filenames), filenames
    assert all("folder_path" in it for it in body["items"])


def test_it_srch_03_search_does_not_leak_other_case(client, auth, ids):
    h = auth("admin.alfa")
    r = client.get(f"/v1/cases/{ids['vacio']}/search", headers=h, params={"q": "demanda"})
    assert r.status_code == 200, r.text
    body = r.json()
    pago_filenames = {d["filename"] for d in client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()}
    assert not ({it["filename"] for it in body["items"]} & pago_filenames)
    assert body["folders"] == []


def test_it_srch_04_media_deletion_request_registers(client, auth, ids, owner_db):
    h = auth("admin.alfa")
    media = client.get(f"/v1/cases/{ids['pago']}/media", headers=h).json()
    assert media, "las semillas deben incluir un medio"
    mid = media[0]["id"]
    r = client.post(f"/v1/cases/{ids['pago']}/media/{mid}/deletion-request", headers=h,
                    json={"reason": "prueba de solicitud"})
    assert r.status_code == 202, r.text
    when, reason = fetch(owner_db,
                         "SELECT deletion_requested_at, deletion_reason FROM media WHERE id = %s", (mid,))[0]
    assert when is not None and reason == "prueba de solicitud"


def test_it_srch_05_media_deletion_request_requires_permission(client, auth, ids):
    h = auth("abogada.alfa")
    media = client.get(f"/v1/cases/{ids['pago']}/media", headers=h).json()
    mid = media[0]["id"]
    r = client.post(f"/v1/cases/{ids['pago']}/media/{mid}/deletion-request", headers=h, json={"reason": "x"})
    assert r.status_code == 403, r.text
