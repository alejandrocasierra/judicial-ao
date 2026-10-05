"""IT-ADM — endpoints del panel de administración."""
import pytest

pytestmark = pytest.mark.integration


def test_it_adm_01_list_users_requires_auth(client):
    r = client.get("/v1/admin/users")
    assert r.status_code == 401


def test_it_adm_02_list_users_requires_permission(client, auth):
    r = client.get("/v1/admin/users", headers=auth("revisor.alfa"))
    assert r.status_code == 403


def test_it_adm_03_admin_can_list_users(client, auth):
    r = client.get("/v1/admin/users", headers=auth("admin.alfa"))
    assert r.status_code == 200
    users = r.json()
    assert len(users) >= 1
    assert all("email" in u and "org_role" in u for u in users)
    assert "password_hash" not in users[0]


def test_it_adm_04_create_user_sends_invite(client, auth):
    r = client.post("/v1/admin/users", headers=auth("admin.alfa"), json={
        "email": "nuevo.usuario@test.local",
        "full_name": "Nuevo Usuario",
        "org_role": "ANALYST",
        "locale": "es",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "nuevo.usuario@test.local"
    assert body["org_role"] == "ANALYST"
    assert body["invite_sent"] is True


def test_it_adm_05_create_user_rejects_duplicate(client, auth):
    r = client.post("/v1/admin/users", headers=auth("admin.alfa"), json={
        "email": "nuevo.usuario@test.local",
        "full_name": "Duplicado",
        "org_role": "ANALYST",
        "locale": "es",
    })
    assert r.status_code == 422 or r.status_code == 409


def test_it_adm_06_get_smtp_settings(client, auth):
    r = client.get("/v1/admin/smtp", headers=auth("admin.alfa"))
    assert r.status_code == 200
    body = r.json()
    assert "from_name" in body and "server" in body and "port" in body


def test_it_adm_06b_smtp_roundtrip_persists(client, auth):
    h = auth("admin.alfa")
    r = client.post("/v1/admin/smtp", headers=h, json={
        "from_name": "Firma Jurídica", "from_email": "notifica@dominio.test", "server": "smtp.dominio.test",
        "port": 587, "security": "STARTTLS", "username": "notifica", "cc_emails": "a@test.local, b@test.local"})
    assert r.status_code == 200, r.text
    got = client.get("/v1/admin/smtp", headers=h).json()
    assert got["from_name"] == "Firma Jurídica" and got["server"] == "smtp.dominio.test"
    assert got["cc_emails"] == ["a@test.local", "b@test.local"]


def test_it_adm_07_trigger_backup(client, auth):
    r = client.post("/v1/admin/backups", headers=auth("admin.alfa"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SUCCEEDED"


def test_it_adm_08_list_agents_includes_system_agent(client, auth):
    r = client.get("/v1/admin/agents", headers=auth("admin.alfa"))
    assert r.status_code == 200
    agents = r.json()
    system = [a for a in agents if a["is_system"]]
    # Agente jurídico anti-alucinación + agentes de tarea (OCR/ASR) + agentes del chat.
    juridico = [a for a in system if "anti-alucinación" in a["name"].lower()]
    assert len(juridico) == 1
    assert len(juridico[0]["skills"]) >= 6  # skills jurídicas de sistema


def test_it_adm_09_agents_readable_by_query_users(client, auth):
    """El chat necesita listar agentes para "/": cualquier usuario con ai.query puede verlos."""
    r = client.get("/v1/agents", headers=auth("abogada.alfa"))
    assert r.status_code == 200
    agents = r.json()
    assert agents and all({"id", "name", "is_system", "kind"} <= set(a) for a in agents)
    # no expone el system_prompt ni permite administrarlos
    assert all("system_prompt" not in a and "skills" not in a for a in agents)
    # un rol sin ai.query no puede
    assert client.get("/v1/agents", headers=auth("lector.alfa")).status_code == 403


def test_it_adm_10_model_custom_base_url_is_used_by_the_chat(client, auth, org_ids):
    """La URL base por modelo permite apuntar a un proxy/VPS: el chat la usa tal cual.
    Funciona con CUALQUIER proveedor/API key del dashboard."""
    import uuid as _uuid

    from app.providers.llm import get_llm_for_model
    h = auth("admin.alfa")
    uid = client.get("/v1/auth/me", headers=h).json()["id"]
    name = f"modelo-custom-{_uuid.uuid4().hex[:6]}"
    r = client.post("/v1/admin/models", headers=h, json={
        "provider": "custom", "model_name": name, "api_key": "clave-de-prueba",
        "api_base_url": "https://mi-proxy.example.com/v1"})
    assert r.status_code == 200, r.text
    model = r.json()
    assert model["api_base_url"] == "https://mi-proxy.example.com/v1"
    try:
        llm = get_llm_for_model(model["id"], org_ids["alfa"], uid)
        assert llm.model == name and llm.api_key == "clave-de-prueba"
        assert llm.api_base_url == "https://mi-proxy.example.com/v1"
        # se puede cambiar la URL después (migrar a una VPS) sin recrear el modelo
        up = client.patch(f"/v1/admin/models/{model['id']}", headers=h,
                          json={"api_base_url": "https://vps.example.net/v1"})
        assert up.status_code == 200 and up.json()["api_base_url"] == "https://vps.example.net/v1"
        assert get_llm_for_model(model["id"], org_ids["alfa"], uid).api_base_url == "https://vps.example.net/v1"
    finally:
        client.delete(f"/v1/admin/models/{model['id']}", headers=h)
