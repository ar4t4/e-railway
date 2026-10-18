import asyncio
import time
from fastapi.testclient import TestClient
from companion.server import create_app
from companion.store import Store
from companion.engine import Engine, claim
from companion.railway import RailwayError
from test_core import watch, result, ready_state


def test_restart_disarms_and_preserves_unknown_booking(tmp_path):
    store = Store(tmp_path)
    s = ready_state()
    job = claim(s, "desktop")
    store.mutate(lambda current:current.update(s))
    async def run():
        engine = Engine(store)
        await engine.start()
        await engine.stop()
    asyncio.run(run())
    recovered = store.read()
    assert recovered["mode"] == "away"
    assert recovered["attempts"][0]["id"] == job["id"]
    assert recovered["attempts"][0]["status"] == "reconcile"
    store.close()


def test_route_error_advances_but_global_rate_limit_backs_off(tmp_path):
    store = Store(tmp_path)
    store.mutate(lambda s:s.update(watches=[watch()]))
    async def route_error(*args):
        raise RailwayError("Unsupported route", "schema")
    engine = Engine(store, route_error)
    asyncio.run(engine.check_once())
    assert store.read()["cursor"] == 1
    async def throttled(*args):
        raise RailwayError("Slow down", "rate_limit", 120)
    engine.search = throttled
    asyncio.run(engine.check_once())
    assert store.read()["cursor"] == 1
    assert engine.next_check >= time.time() + 119
    store.close()


def test_duplicate_report_and_late_report_never_revive_attempt(tmp_path, monkeypatch):
    monkeypatch.delenv("RC_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("RC_PUBLIC_URL", raising=False)
    app = create_app(tmp_path, background=False)
    with TestClient(app) as client:
        client.post("/api/auth/setup", json={"password":"test-password-long"})
        token = client.post("/api/agent/pair").json()["token"]
        header = {"Authorization":"Bearer "+token}
        app.state.store.mutate(lambda s:s.update(ready_state()))
        job = client.post("/api/agent/claim", json={"id":"desktop"}, headers=header).json()["job"]
        report = {"id":"desktop", "status":"seats_selected", "message":"Seats selected", "selected":["GA-1","GA-2"]}
        path = "/api/agent/report/"+job["id"]
        assert client.post(path, json=report, headers=header).json()["status"] == "seats_selected"
        assert client.post(path, json=report, headers=header).json()["duplicate"]
        client.post("/api/attempts/"+job["id"]+"/resolve", json={"outcome":"released"})
        assert client.post(path, json=report, headers=header).json()["ignored"]
        assert app.state.store.read()["attempts"][0]["status"] == "resolved"
