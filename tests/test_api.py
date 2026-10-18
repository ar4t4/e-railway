import time
import pytest
from fastapi.testclient import TestClient
from companion.server import create_app
from test_core import watch, result


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("RC_PUBLIC_URL", raising=False)
    monkeypatch.delenv("RC_SETUP_TOKEN", raising=False)
    app = create_app(tmp_path, background=False)
    with TestClient(app) as c:
        yield c


def login(client):
    response = client.post("/api/auth/setup", json={"password":"this-is-a-test-password"})
    assert response.status_code == 200


def pair(client):
    token = client.post("/api/agent/pair").json()["token"]
    return {"Authorization":"Bearer " + token}


def test_auth_origin_and_password_protection(client):
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/auth/setup", json={"password":""}).status_code == 422
    login(client)
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/mode", json={"mode":"away"}, headers={"Origin":"https://evil.example"}).status_code == 403
    assert client.post("/api/auth/setup", json={"password":"different-password"}).status_code == 409
    assert "HttpOnly" in client.post("/api/auth/login", json={"password":"this-is-a-test-password"}).headers["set-cookie"]
    client.post("/api/auth/logout")
    assert client.get("/api/state").status_code == 401


def test_setup_token_required_for_remote_config(client, monkeypatch):
    monkeypatch.setenv("RC_PUBLIC_URL", "https://my.example")
    assert client.post("/api/auth/setup", json={"password":"password-long-enough"}).status_code == 403
    monkeypatch.setenv("RC_SETUP_TOKEN", "bootstrap-example")
    assert client.post("/api/auth/setup", json={"password":"password-long-enough", "setup_token":"bootstrap-example"}).status_code == 200


def test_single_character_password_can_setup_and_login(client):
    assert client.post("/api/auth/setup", json={"password": "a"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"password": "b"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "a"}).status_code == 200


def test_telegram_setup_requires_private_challenge_and_hides_token(client, monkeypatch):
    from unittest.mock import AsyncMock
    token = "123456789:" + "a" * 30
    assert client.post("/api/telegram/setup", json={"token": token}).status_code == 401
    login(client)
    engine = client.app.state.engine
    call = AsyncMock(side_effect=[{"username": "companion_test_bot"}, {"url": ""}])
    monkeypatch.setattr(engine, "telegram_call", call)
    response = client.post("/api/telegram/setup", json={"token": token})
    assert response.status_code == 200
    assert token not in response.text
    challenge = response.json()["url"].split("?start=")[1]
    def update(i, text, kind="private", sender=12345):
        return {"update_id": i, "message": {"text": text, "chat": {"id": 12345, "type": kind}, "from": {"id": sender}}}
    call.side_effect = None
    call.return_value = [update(1, "/start wrong"), update(2, "/start " + challenge, "group"), update(3, "/start " + challenge, sender=67890)]
    assert client.post("/api/telegram/setup/check").json()["connected"] is False
    assert not client.app.state.store.secret("telegram_token")
    call.return_value = [update(4, "/start " + challenge)]
    assert client.post("/api/telegram/setup/check").json()["connected"] is True
    assert call.call_args.args[1]["offset"] == 4
    store = client.app.state.store
    assert store.secret("telegram_chat") == "12345"
    assert store.secret("telegram_offset") == 5
    assert store.secret("telegram_token") == token
    public = client.get("/api/state")
    assert token not in public.text and challenge not in public.text
    assert public.json()["settings"]["telegram_enabled"]
    assert store.read()["outbox"][-1]["test"]
    assert engine.telegram_setup is None


def test_telegram_setup_webhook_failure_and_expiry_preserve_connection(client, monkeypatch):
    from unittest.mock import AsyncMock
    login(client)
    engine = client.app.state.engine
    store = client.app.state.store
    store.set_secret("telegram_token", "existing")
    token = "123456789:" + "b" * 30
    call = AsyncMock(side_effect=[{"username": "companion_test_bot"}, {"url": "https://example.com/webhook"}])
    monkeypatch.setattr(engine, "telegram_call", call)
    assert client.post("/api/telegram/setup", json={"token": token}).status_code == 409
    assert engine.telegram_setup is None
    call.side_effect = RuntimeError("sensitive URL including " + token)
    response = client.post("/api/telegram/setup", json={"token": token})
    assert response.status_code == 400 and token not in response.text
    engine.telegram_setup = {"expires": time.time() - 1, "token": token}
    assert client.post("/api/telegram/setup/check").status_code == 400
    assert engine.telegram_setup is None
    assert store.secret("telegram_token") == "existing"


def test_watchlist_crud_and_disabled_import(client):
    login(client)
    response = client.post("/api/watchlists", json=watch())
    assert response.status_code == 200
    w = response.json()
    assert client.put("/api/watchlists/"+w["id"], json=dict(w, passengers=3)).json()["revision"] == 2
    export = client.get("/api/export").json()
    assert "id" not in export["watchlists"][0]
    assert client.post("/api/import", json=export).status_code == 200
    state = client.get("/api/state").json()
    assert not state["watches"][-1]["enabled"]
    assert client.delete("/api/watchlists/"+w["id"]).status_code == 200


def test_desktop_pair_mode_claim_and_reconciliation(client):
    login(client)
    assert client.post("/api/mode", json={"mode":"at_pc"}).status_code == 409
    headers = pair(client)
    assert client.post("/api/agent/heartbeat", json={"id":"desktop"}).status_code == 401
    client.post("/api/agent/heartbeat", headers=headers, json={"id":"desktop", "ready":True})
    assert client.post("/api/mode", json={"mode":"at_pc"}).status_code == 200
    client.app.state.store.mutate(lambda s:s.update(watches=[watch()], results=[result()]))
    job = client.post("/api/agent/claim", headers=headers, json={"id":"desktop"}).json()["job"]
    assert job
    assert client.post("/api/agent/pair").status_code == 409
    assert client.post("/api/agent/permit/"+job["id"], headers=headers, json={"id":"desktop"}).json()["allowed"]
    client.post("/api/mode", json={"mode":"away"})
    report = client.post("/api/agent/report/"+job["id"], headers=headers, json={"id":"desktop", "status":"payment_ready", "message":"Payment ready"})
    assert report.json()["status"] == "reconcile"
    client.post("/api/attempts/"+job["id"]+"/resolve", json={"outcome":"booked"})
    s = client.get("/api/state").json()
    assert not s["watches"][0]["enabled"]
    assert s["attempts"][0]["status"] == "user_reported_booked"
    assert s["mode"] == "away"


def test_secret_input_never_in_state_or_export(client):
    login(client)
    credential = {"token":"private-railway-token", "uudid":"device-id", "ssdk":"device-secret"}
    assert client.put("/api/connections/railway", json=credential).status_code == 200
    assert "private-railway-token" not in client.get("/api/state").text
    assert "device-secret" not in client.get("/api/export").text
    assert client.post("/api/telegram/test").status_code == 400


def test_different_agent_cannot_replace_live_agent(client):
    login(client)
    headers = pair(client)
    assert client.post("/api/agent/heartbeat", headers=headers, json={"id":"one"}).status_code == 200
    assert client.post("/api/agent/heartbeat", headers=headers, json={"id":"two"}).status_code == 409


def test_assets_and_no_inline_script_policy(client):
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert "'unsafe-inline'" not in client.get("/").headers["content-security-policy"]
    assert client.get("/assets/../data/companion.sqlite").status_code != 200
