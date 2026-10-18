from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .store import Store, event
from .models import Watchlist, Preferences, queries, matches, in_quiet_hours
from .engine import Engine, ACTIVE, claim, permitted, set_mode, enqueue, FRESH_SECONDS
from .local_desktop import start_local_agent, open_regular_browser

ROOT = Path(__file__).resolve().parent.parent


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    setup_token: str = Field(default="", max_length=256)


class Mode(BaseModel):
    mode: str


class Heartbeat(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    ready: bool = False
    message: str = Field(default="", max_length=200)


class Report(BaseModel):
    id: str
    status: str
    message: str = Field(max_length=500)
    selected: list[str] = Field(default_factory=list, max_length=4)


def create_app(directory=None, background=True):
    store = Store(directory or os.getenv("RC_DATA_DIR", str(ROOT / "data")))
    engine = Engine(store)
    attempts_by_ip = {}

    @asynccontextmanager
    async def lifespan(app):
        if background:
            await engine.start()
        yield
        if background:
            await engine.stop()
        store.close()

    app = FastAPI(title="Railway Companion", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.engine = store, engine

    @app.middleware("http")
    async def boundaries(request, call_next):
        public_url = os.getenv("RC_PUBLIC_URL", "")
        allowed_hosts = {urlsplit(public_url).hostname, "127.0.0.1", "localhost", "::1"} if public_url else {"127.0.0.1", "localhost", "::1"}
        if not background:
            allowed_hosts.add("testserver")
        if request.url.hostname not in allowed_hosts:
            return JSONResponse({"detail": "Unexpected service host"}, status_code=400)
        try:
            size = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return JSONResponse({"detail": "Invalid content length"}, status_code=400)
        if size > 100_000:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = os.getenv("RC_PUBLIC_URL", str(request.base_url)).rstrip("/")
            expected = urlsplit(expected)
            extension_request = (request.url.path == '/api/browser/session'
                and origin and urlsplit(origin).scheme == 'chrome-extension')
            if origin and urlsplit(origin)[:2] != expected[:2] and not extension_request:
                return JSONResponse({"detail": "Cross-site request rejected"}, status_code=403)
        response = await call_next(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    def owner(request: Request):
        value = request.cookies.get("rc_session", "")
        if not value or not store.session(digest(value)):
            raise HTTPException(401, "Sign in to continue")

    def agent(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        expected = store.secret("agent_hash", "")
        if not expected or not hmac.compare_digest(digest(token), expected):
            raise HTTPException(401, "Desktop agent is not paired")

    def set_session(response):
        token = secrets.token_urlsafe(40)
        store.session(digest(token), time.time() + 7 * 86400)
        response.set_cookie("rc_session", token, httponly=True, samesite="strict", max_age=7 * 86400,
            secure=os.getenv("RC_SECURE_COOKIES") == "1")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        return {"configured": bool(store.secret("owner")), "authenticated": store.session(digest(request.cookies.get("rc_session", "")))}

    @app.post("/api/auth/setup")
    def setup(body: Login, request: Request, response: Response):
        if store.secret("owner"):
            raise HTTPException(409, "Owner already configured")
        expected = os.getenv("RC_SETUP_TOKEN", "")
        local = request.client.host in {"127.0.0.1", "::1", "testclient"} and not os.getenv("RC_PUBLIC_URL")
        if (expected and not hmac.compare_digest(body.setup_token, expected)) or (not expected and not local):
            raise HTTPException(403, "Enter the setup token configured on your server")
        salt = secrets.token_hex(16)
        if not store.create_secret("owner", {"salt": salt, "hash": password_hash(body.password, salt)}):
            raise HTTPException(409, "Owner already configured")
        set_session(response)
        return {"ok": True}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response):
        ip = request.client.host
        now = time.time()
        prior = [t for t in attempts_by_ip.get(ip, []) if now - t < 300]
        if len(prior) >= 8:
            raise HTTPException(429, "Too many attempts. Try again in five minutes.")
        attempts_by_ip[ip] = prior + [now]
        record = store.secret("owner")
        if not record or not hmac.compare_digest(password_hash(body.password, record["salt"]), record["hash"]):
            raise HTTPException(401, "Incorrect password")
        attempts_by_ip.pop(ip, None)
        set_session(response)
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        store.session(digest(request.cookies.get("rc_session", "")), time.time() - 1)
        response.delete_cookie("rc_session")
        return {"ok": True}

    @app.get("/api/state", dependencies=[Depends(owner)])
    def state():
        s = store.read()
        now = time.time()
        s.pop("seen", None)
        s.pop("commands", None)
        s["server_time"] = now
        s["quiet"] = in_quiet_hours(s["settings"])
        s["coverage"] = len(queries(s["watches"]))
        s["agent"]["connected"] = now - s["agent"].get("seen", 0) <= 20
        s["connections"] = {"railway": bool(store.secret("railway")), "telegram": bool(store.secret("telegram_token") and store.secret("telegram_chat")),
            "paired": bool(store.secret("agent_hash")), "passengers": len(store.secret("passengers", []))}
        for result in s["results"]:
            result["fresh"] = not result.get("error") and now - result["checked"] <= FRESH_SECONDS
            result["watch_ids"] = [w["id"] for w in s["watches"] if w["enabled"] and matches(w, result)]
        s["outbox"] = [{k: n[k] for k in ("id", "created", "status", "tries")} for n in s["outbox"][-30:]]
        return s

    @app.post("/api/watchlists", dependencies=[Depends(owner)])
    def add_watch(body: Watchlist):
        watch = dict(body.model_dump(), id=secrets.token_hex(6), revision=1, created=time.time())
        def add(s):
            if len(s["watches"]) >= 50:
                raise HTTPException(400, "Maximum 50 watchlists")
            s["watches"].append(watch)
            event(s, "watchlist", "Created " + watch["name"])
        store.mutate(add)
        engine.next_check = min(engine.next_check, time.time())
        return watch

    @app.put("/api/watchlists/{watch_id}", dependencies=[Depends(owner)])
    def update_watch(watch_id: str, body: Watchlist):
        def update(s):
            w = next((w for w in s["watches"] if w["id"] == watch_id), None)
            if not w:
                raise HTTPException(404, "Watchlist not found")
            w.update(body.model_dump(), revision=w["revision"] + 1)
            return w
        return store.mutate(update)

    @app.delete("/api/watchlists/{watch_id}", dependencies=[Depends(owner)])
    def delete_watch(watch_id: str):
        def remove(s):
            if any(a["watch_id"] == watch_id and a["status"] in ACTIVE for a in s["attempts"]):
                raise HTTPException(409, "Resolve the outstanding booking before deleting this watchlist")
            s["watches"] = [w for w in s["watches"] if w["id"] != watch_id]
        store.mutate(remove)
        return {"ok": True}

    @app.post("/api/watchlists/{watch_id}/booked", dependencies=[Depends(owner)])
    def mark_external_booking(watch_id: str):
        def update(s):
            w = next((w for w in s["watches"] if w["id"] == watch_id), None)
            if not w:
                raise HTTPException(404, "Watchlist not found")
            if any(a["watch_id"] == watch_id and a["status"] in ACTIVE for a in s["attempts"]):
                raise HTTPException(409, "Resolve the existing desktop attempt from Bookings first")
            w.update(enabled=False, revision=w["revision"] + 1)
            event(s, "booking", "You marked " + w["name"] + " as booked in Rail Sheba. Watchlist paused.")
        store.mutate(update)
        return {"ok": True}

    @app.post("/api/mode", dependencies=[Depends(owner)])
    def mode(body: Mode):
        if body.mode not in {"away", "at_pc"}:
            raise HTTPException(400, "Choose At PC or Away")
        try:
            store.mutate(lambda s: set_mode(s, body.mode))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"ok": True}

    @app.post("/api/monitor/{action}", dependencies=[Depends(owner)])
    def monitor(action: str):
        if action not in {"pause", "resume"}:
            raise HTTPException(400, "Unknown monitoring action")
        def update(s):
            s["paused"] = action == "pause"
            if s["paused"]:
                set_mode(s, "away")
            event(s, "monitor", "Monitoring " + ("paused" if s["paused"] else "resumed"))
        store.mutate(update)
        return {"ok": True}

    @app.put("/api/settings", dependencies=[Depends(owner)])
    def settings(body: Preferences):
        store.mutate(lambda s: s.update(settings=body.model_dump()))
        return {"ok": True}

    @app.put("/api/connections/{name}", dependencies=[Depends(owner)])
    def connection(name: str, body: dict):
        if name == "railway":
            if not all(isinstance(body.get(k), str) and 1 <= len(body[k]) <= 10000 for k in ("token", "uudid")):
                raise HTTPException(400, "Session needs token and uudid values")
            store.set_secret("railway", {k: str(body.get(k, ""))[:10000] for k in ("token", "uudid", "ssdk", "user_agent")})
            store.set_secret('railway_source', 'manual')
            engine.next_check = 0
        elif name == "telegram":
            import re
            token, chat = str(body.get("token", "")), str(body.get("chat_id", ""))
            if not re.fullmatch(r"\d{5,20}:[A-Za-z0-9_-]{20,100}", token) or not re.fullmatch(r"[1-9]\d{3,19}", chat):
                raise HTTPException(400, "Enter a valid bot token and your positive private-chat ID")
            store.set_secret("telegram_token", token)
            store.set_secret("telegram_chat", chat)
            store.set_secret("telegram_offset", 0)
        elif name == "passengers":
            passengers = body.get("passengers", [])
            if not isinstance(passengers, list) or not 1 <= len(passengers) <= 4:
                raise HTTPException(400, "Provide 1–4 passengers")
            for person in passengers:
                if not isinstance(person, dict) or not isinstance(person.get("name"), str) or not person["name"].strip() or len(person["name"]) > 100:
                    raise HTTPException(400, "Each passenger needs a name")
            store.set_secret("passengers", [{"name": p["name"].strip(), "mobile": str(p.get("mobile", ""))[:30],
                "email": str(p.get("email", ""))[:200]} for p in passengers])
        else:
            raise HTTPException(404, "Unknown connection")
        return {"ok": True}

    def require_local_browser(request):
        if (os.name != 'nt' or os.getenv('RC_PUBLIC_URL') or not request.client
                or request.client.host not in {'127.0.0.1', '::1'}
                or request.url.hostname not in {'127.0.0.1', 'localhost', '::1'}):
            raise HTTPException(403, 'Normal-browser connection is available from this Windows PC only.')

    @app.post('/api/browser/connect', dependencies=[Depends(owner)])
    def connect_regular_browser(request: Request):
        require_local_browser(request)
        if any(a['status'] in ACTIVE for a in store.read()['attempts']):
            raise HTTPException(409, 'Finish or resolve the current booking first')
        from tools.prepare_browser_bridge import prepare
        directory = prepare(store.directory)
        try:
            open_regular_browser(store.directory)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None
        store.mutate(lambda s: set_mode(s, 'away'))
        bridge = store.read().get('browser_connection', {})
        return {'ok': True, 'extension_directory': str(directory.resolve()),
                'extension_seen': time.time() - bridge.get('seen', 0) < 90}

    @app.post('/api/browser/extensions', dependencies=[Depends(owner)])
    def browser_extensions(request: Request):
        require_local_browser(request)
        try:
            open_regular_browser(store.directory, 'extensions')
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None
        return {'ok': True}

    @app.post('/api/browser/session')
    def browser_session(request: Request, body: dict):
        require_local_browser(request)
        expected = store.secret('browser_bridge_hash')
        supplied = request.headers.get('authorization', '').removeprefix('Bearer ')
        if not expected or not hmac.compare_digest(digest(supplied), expected):
            raise HTTPException(401, 'Browser connection key rejected')
        status = body.get('state')
        if status == 'signed_in':
            session = body.get('session')
            if not isinstance(session, dict):
                raise HTTPException(400, 'A railway session is required')
            for key in ('token', 'uudid', 'ssdk', 'user_agent'):
                value = session.get(key, '')
                if not isinstance(value, str) or len(value) > 10000 or any(c in value for c in '\r\n'):
                    raise HTTPException(400, 'Invalid session field')
            connection('railway', session)
            store.set_secret('railway_source', 'browser')
        elif status == 'signed_out':
            if store.secret('railway_source') == 'browser':
                store.set_secret('railway', None)
                engine.next_check = 0
        else:
            raise HTTPException(400, 'Unknown browser session state')
        store.mutate(lambda s: s.update(browser_connection={'seen': time.time(), 'status': status}))
        return {'ok': True}

    @app.post("/api/telegram/setup", dependencies=[Depends(owner)])
    async def telegram_setup(body: dict):
        import re
        token = str(body.get("token", "")).strip()
        if not re.fullmatch(r"\d{5,20}:[A-Za-z0-9_-]{20,100}", token):
            raise HTTPException(400, "Paste the bot token provided by BotFather")
        async with engine.telegram_lock:
            try:
                bot = await engine.telegram_call("getMe", {}, token=token)
                webhook = await engine.telegram_call("getWebhookInfo", {}, token=token)
            except Exception:
                raise HTTPException(400, "Could not reach your bot. Check the token and your internet connection.") from None
            if webhook.get("url"):
                raise HTTPException(409, "This bot uses a webhook for another application. Create a separate bot with BotFather.")
            username = bot.get("username", "")
            if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
                raise HTTPException(400, "Telegram did not return a valid bot username")
            challenge = "rc_" + secrets.token_urlsafe(24)
            engine.telegram_setup = {"token": token, "challenge": challenge, "expires": time.time() + 600, "offset": 0}
        return {"url": f"https://t.me/{username}?start={challenge}"}

    @app.post("/api/telegram/setup/check", dependencies=[Depends(owner)])
    async def telegram_setup_check():
        async with engine.telegram_lock:
            pending = engine.telegram_setup
            if not pending or pending["expires"] < time.time():
                engine.telegram_setup = None
                raise HTTPException(400, "Setup expired. Enter your bot token again.")
            try:
                updates = await engine.telegram_call("getUpdates", {"offset": pending["offset"], "timeout": 0,
                    "allowed_updates": ["message", "callback_query"]}, token=pending["token"])
            except Exception:
                raise HTTPException(400, "Could not check messages. Check your connection and ensure no other app is polling this bot.") from None
            matched_chat = None
            for update in updates or []:
                pending["offset"] = max(pending["offset"], update["update_id"] + 1)
                msg = update.get("message", {})
                chat = msg.get("chat", {})
                if (msg.get("text") == "/start " + pending["challenge"] and chat.get("type") == "private"
                        and isinstance(chat.get("id"), int) and chat["id"] > 0
                        and chat["id"] == msg.get("from", {}).get("id")):
                    matched_chat = str(chat["id"])
            if not matched_chat:
                return {"connected": False, "message": "Open the Telegram link, press Start, then check again."}
            store.set_secret("telegram_token", pending["token"])
            store.set_secret("telegram_chat", matched_chat)
            store.set_secret("telegram_offset", pending["offset"])
            engine.telegram_setup = None
            def connected(s):
                s["settings"]["telegram_enabled"] = True
                enqueue(s, "Railway Companion is connected. Telegram alerts are enabled. Enable Telegram on each watchlist you want alerts for.", test=True)
            store.mutate(connected)
        return {"connected": True, "message": "Telegram connected and enabled. A confirmation message is queued."}

    @app.post("/api/telegram/test", dependencies=[Depends(owner)])
    def telegram_test():
        if not store.secret("telegram_token"):
            raise HTTPException(400, "Configure Telegram first")
        store.mutate(lambda s: enqueue(s, "Railway Companion test: your Telegram connection is ready.", test=True))
        return {"ok": True, "message": "Test queued. Delivery status appears below."}

    @app.post("/api/agent/pair", dependencies=[Depends(owner)])
    def pair():
        if any(a["status"] in ACTIVE for a in store.read()["attempts"]):
            raise HTTPException(409, "Resolve outstanding bookings before replacing the desktop agent")
        token = secrets.token_urlsafe(40)
        store.set_secret("agent_hash", digest(token))
        def update(s):
            set_mode(s, "away")
            s["agent"] = {"seen": 0, "ready": False}
        store.mutate(update)
        return {"token": token}

    @app.post("/api/desktop/{command}", dependencies=[Depends(owner)])
    def desktop_command(command: str, request: Request):
        if command not in {"connect", "sync_session"}:
            raise HTTPException(400, "Unknown desktop command")
        current = store.read()
        if any(a['status'] in ACTIVE for a in current['attempts']):
            raise HTTPException(409, "Finish or resolve the current booking first")
        if command == 'connect' and time.time() - current['agent'].get('seen', 0) > 20:
            if (os.name != 'nt' or os.getenv('RC_PUBLIC_URL') or not request.client
                    or request.client.host not in {'127.0.0.1', '::1'}
                    or request.url.hostname not in {'127.0.0.1', 'localhost', '::1'}):
                raise HTTPException(409, 'Launch the desktop agent on your PC, then click Connect Railway.')
            try:
                start_local_agent(store, str(request.base_url).rstrip('/'))
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from None
        def queue(s):
            if time.time() - s["agent"].get("seen", 0) > 20:
                raise HTTPException(409, "Start your desktop agent first")
            if any(a["status"] in ACTIVE for a in s["attempts"]):
                raise HTTPException(409, "Finish or resolve the current booking first")
            if command not in s["commands"]:
                s["commands"].append(command)
        store.mutate(queue)
        return {"ok": True}

    @app.post("/api/agent/heartbeat", dependencies=[Depends(agent)])
    def heartbeat(body: Heartbeat):
        def update(s):
            current = s["agent"]
            if current.get("id") and current["id"] != body.id and time.time() - current.get("seen", 0) < 25:
                raise HTTPException(409, "Another desktop agent is connected")
            if time.time() - current.get("seen", 0) > 25 and s["mode"] == "at_pc":
                set_mode(s, "away")
            s["agent"] = {"id": body.id, "seen": time.time(), "ready": body.ready, "message": body.message}
            commands, s["commands"] = s["commands"], []
            return {"mode": s["mode"], "commands": commands, "sound": s["settings"]["sound"] and not in_quiet_hours(s["settings"])}
        return store.mutate(update)

    @app.post("/api/agent/claim", dependencies=[Depends(agent)])
    def agent_claim(body: Heartbeat):
        job = store.mutate(lambda s: claim(s, body.id))
        if job:
            job = dict(job, passenger_details=store.secret("passengers", [])[:job["passengers"]])
        return {"job": job}

    @app.post("/api/agent/permit/{attempt_id}", dependencies=[Depends(agent)])
    def permit(attempt_id: str, body: Heartbeat):
        return {"allowed": permitted(store.read(), attempt_id, body.id)}

    @app.post("/api/agent/report/{attempt_id}", dependencies=[Depends(agent)])
    def report(attempt_id: str, body: Report):
        if body.status not in {"preparing", "seats_selected", "needs_attention", "payment_ready", "reconcile"}:
            raise HTTPException(400, "Unknown booking state")
        def update(s):
            a = next((a for a in s["attempts"] if a["id"] == attempt_id and a["agent_id"] == body.id), None)
            if not a:
                raise HTTPException(404, "Attempt not found")
            if a["status"] not in ACTIVE:
                return {"ok": True, "status": a["status"], "ignored": True}
            if a["status"] == body.status and a["message"] == body.message and a["selected"] == body.selected:
                return {"ok": True, "status": a["status"], "duplicate": True}
            # Reports cannot revive an attempt invalidated by mode/revision changes.
            allowed = permitted(s, attempt_id, body.id)
            status = body.status if allowed else "reconcile"
            a.update(status=status, message=body.message, selected=body.selected, updated=time.time())
            item = event(s, "booking", body.message, attempt_id=a["id"], sound=a["sound"] and status != "preparing" and s["mode"] == "at_pc")
            w = next((w for w in s["watches"] if w["id"] == a["watch_id"]), None)
            if status != "preparing":
                enqueue(s, f'{a["result"]["train"]}: {body.message}', watch=w)
            return {"ok": True, "status": status, "event": item["id"]}
        return store.mutate(update)

    @app.post("/api/agent/session", dependencies=[Depends(agent)])
    def sync_session(body: dict):
        return connection("railway", body)

    @app.post("/api/agent/away", dependencies=[Depends(agent)])
    def desktop_away():
        store.mutate(lambda s: set_mode(s, "away"))
        return {"ok": True}

    @app.post("/api/attempts/{attempt_id}/resolve", dependencies=[Depends(owner)])
    def resolve(attempt_id: str, body: dict):
        outcome = body.get("outcome")
        if outcome not in {"booked", "released"}:
            raise HTTPException(400, "Confirm the trip was booked or the old selection has been released/expired")
        def update(s):
            a = next((a for a in s["attempts"] if a["id"] == attempt_id), None)
            if not a:
                raise HTTPException(404, "Attempt not found")
            if a["status"] == "preparing":
                raise HTTPException(409, "Switch to Away mode and inspect the browser before resolving an in-flight attempt")
            a.update(status="user_reported_booked" if outcome == "booked" else "resolved", updated=time.time())
            set_mode(s, "away")
            if outcome == "booked":
                for w in s["watches"]:
                    if w["id"] == a["watch_id"]:
                        w.update(enabled=False, revision=w["revision"] + 1)
            event(s, "booking", "Booking resolved by you · " + outcome)
        store.mutate(update)
        return {"ok": True}

    @app.get("/api/export", dependencies=[Depends(owner)])
    def export():
        return {"version": 1, "watchlists": [Watchlist.model_validate(w).model_dump() for w in store.read()["watches"]]}

    @app.post("/api/import", dependencies=[Depends(owner)])
    def import_watches(body: dict):
        try:
            items = [Watchlist.model_validate(w).model_dump() for w in body["watchlists"]]
        except Exception:
            raise HTTPException(400, "Invalid watchlist export") from None
        def add(s):
            if len(items) + len(s["watches"]) > 50:
                raise HTTPException(400, "Maximum 50 watchlists")
            for w in items:
                s["watches"].append(dict(w, id=secrets.token_hex(6), revision=1, created=time.time(), enabled=False))
        store.mutate(add)
        return {"ok": True, "message": "Imported paused. Review and enable your watchlists."}

    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web" / "index.html")

    return app
