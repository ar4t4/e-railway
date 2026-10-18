from __future__ import annotations

import asyncio
import secrets
import time
import httpx
from . import railway
from .models import queries, matches, in_quiet_hours
from .store import event

ACTIVE = {"preparing", "seats_selected", "needs_attention", "payment_ready", "reconcile"}
FRESH_SECONDS = 60


def enqueue(state, text, *, watch=None, test=False):
    if not test and (not state["settings"]["telegram_enabled"] or (watch and not watch["telegram"])):
        return
    state["outbox"].append({"id": secrets.token_hex(8), "text": text, "created": time.time(), "test": test,
        "watch_id": watch["id"] if watch else None,
        "expires": time.time() + 300, "tries": 0, "next": 0, "status": "pending"})
    state["outbox"] = state["outbox"][-300:]


def set_mode(state, mode):
    if mode == "at_pc" and (time.time() - state["agent"].get("seen", 0) > 20 or not state["agent"].get("ready")):
        raise ValueError("Connect the desktop agent and sign in to the railway before enabling At PC mode.")
    state["mode"] = mode
    state["generation"] = state.get("generation", 0) + 1
    if mode == "away":
        for attempt in state["attempts"]:
            if attempt["status"] == "preparing":
                attempt["status"] = "reconcile"
                attempt["message"] = "Mode changed. Check the desktop browser for any selection already made."
    event(state, "mode", "At PC automation enabled" if mode == "at_pc" else "Away mode · monitoring only")


def claim(state, agent_id, now=None):
    now = now or time.time()
    agent = state["agent"]
    if state["mode"] != "at_pc" or state["paused"] or not agent.get("ready"):
        return None
    if agent.get("id") != agent_id or now - agent.get("seen", 0) > 20:
        return None
    if any(a["status"] in ACTIVE for a in state["attempts"]):
        return None
    for watch in sorted(state["watches"], key=lambda w: (w["priority"], w["id"])):
        if not watch["enabled"] or watch["action"] == "watch":
            continue
        candidates = [r for r in state["results"] if not r.get("error") and now - r["checked"] <= FRESH_SECONDS and matches(watch, r)]
        candidates.sort(key=lambda r: (watch["dates"].index(r["date"]), watch["origins"].index(r["origin"]),
            watch["destinations"].index(r["destination"]), watch["classes"].index(r["class"]), r["train"]))
        for result in candidates:
            # A resolved attempt is not silently repeated against the same stale observation.
            if any(a["result"]["id"] == result["id"] and now - a["created"] < 300 for a in state["attempts"]):
                continue
            attempt = {"id": secrets.token_hex(8), "agent_id": agent_id, "watch_id": watch["id"],
                "watch_revision": watch["revision"], "watch_name": watch["name"], "action": watch["action"],
                "passengers": watch["passengers"], "sound": watch["sound"] and state["settings"]["sound"] and not in_quiet_hours(state["settings"]),
                "result": result, "status": "preparing", "message": "Desktop agent is checking the official seat layout.",
                "created": now, "updated": now, "generation": state.get("generation", 0), "selected": []}
            state["attempts"] = [attempt] + state["attempts"][:99]
            event(state, "booking", "Preparing " + result["train"], attempt_id=attempt["id"])
            return attempt
    return None


def permitted(state, attempt_id, agent_id):
    attempt = next((a for a in state["attempts"] if a["id"] == attempt_id), None)
    if not attempt or attempt["agent_id"] != agent_id or attempt["status"] != "preparing":
        return False
    watch = next((w for w in state["watches"] if w["id"] == attempt["watch_id"]), None)
    return bool(state["mode"] == "at_pc" and not state["paused"] and watch and watch["enabled"]
        and watch["revision"] == attempt["watch_revision"] and attempt["generation"] == state.get("generation", 0)
        and state["agent"].get("id") == agent_id and time.time() - state["agent"].get("seen", 0) <= 20)


class Engine:
    def __init__(self, store, search=railway.search):
        self.store, self.search = store, search
        self.tasks = []
        self.next_check = 0
        self.failures = 0
        self.telegram_lock = asyncio.Lock()
        self.telegram_setup = None

    async def start(self):
        def recover(s):
            s["mode"] = "away"
            s["agent"] = {"seen": 0, "ready": False}
            s["commands"] = []
            for a in s["attempts"]:
                if a["status"] in ACTIVE:
                    a["status"] = "reconcile"
                    a["message"] = "Service restarted. Check the official browser before resolving this attempt."
            event(s, "system", "Service started · Away mode")
        self.store.mutate(recover)
        self.tasks = [asyncio.create_task(self.monitor()), asyncio.create_task(self.notifications()), asyncio.create_task(self.telegram_commands())]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def monitor(self):
        while True:
            try:
                def health(s):
                    s["health"]["heartbeat"] = time.time()
                    if s["mode"] == "at_pc" and time.time() - s["agent"].get("seen", 0) > 25:
                        set_mode(s, "away")
                        enqueue(s, "Desktop disconnected. Monitoring continues in Away mode; automatic booking is off.")
                self.store.mutate(health)
                s = self.store.read()
                if not s["paused"] and time.time() >= self.next_check:
                    await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.next_check = time.time() + 30
                self.store.mutate(lambda s: s["health"].update(status="error", message="Monitor encountered a problem; retrying in 30 seconds."))
            await asyncio.sleep(1)

    async def check_once(self):
        s = self.store.read()
        plans = queries(s["watches"])
        if not plans:
            self.store.mutate(lambda s: s["health"].update(status="idle", message="Add an active watchlist with future dates."))
            self.next_check = time.time() + 5
            return
        keys = list(plans)
        query = keys[s["cursor"] % len(keys)]
        self.next_check = time.time() + s["settings"]["interval"]
        try:
            results, new_key = await self.search(query, self.store.secret("railway"))
            if new_key:
                cred = self.store.secret("railway", {})
                cred["ssdk"] = new_key
                self.store.set_secret("railway", cred)
            self.failures = 0
            self.accept(query, results)
        except railway.RailwayError as exc:
            self.failures += 1
            delay = max(exc.retry_after, min(300, 5 * 2 ** min(self.failures, 6)))
            self.next_check = time.time() + delay
            def fail(s):
                previous = s["health"].get("status")
                s["health"].update(status=exc.kind, message=str(exc), next_check=self.next_check)
                if exc.kind not in {"session", "rate_limit"}:
                    s["cursor"] += 1  # One broken route must not starve every other watchlist.
                for r in s["results"]:
                    if r["query"] == list(query):
                        r["error"] = True
                if previous != exc.kind:
                    event(s, "attention", str(exc))
                    enqueue(s, str(exc))
            self.store.mutate(fail)

    def accept(self, query, results):
        now = time.time()
        def save(s):
            old_ids = {r["id"] for r in s["results"] if r["query"] == list(query)}
            current_ids = {r["id"] for r in results}
            for key, val in s["seen"].items():
                if key.rsplit(":", 1)[-1] in old_ids - current_ids:
                    val["good"] = False
            s["results"] = [r for r in s["results"] if r["query"] != list(query) and now - r["checked"] < 86400]
            for result in results:
                result = dict(result, checked=now, error=False)
                s["results"].append(result)
                for w in s["watches"]:
                    if not w["enabled"] or result["origin"] not in w["origins"] or result["destination"] not in w["destinations"] or result["date"] not in w["dates"] or result["class"] not in w["classes"]:
                        continue
                    key = w["id"] + ":" + result["id"]
                    previous = s["seen"].get(key, {"at": 0, "good": False, "seats": 0})
                    good = matches(w, result)
                    changed = not previous["good"] or previous["seats"] != result["seats"]
                    should_alert = good and changed and now - previous["at"] >= s["settings"]["alert_cooldown"]
                    s["seen"][key] = {"good": good, "seats": result["seats"], "at": now if should_alert else previous["at"], "observed": now}
                    if should_alert:
                        event(s, "match", f'{result["train"]} · {result["seats"]} {result["class"]} seats',
                            watch_id=w["id"], result_id=result["id"], sound=w["sound"] and s["mode"] == "at_pc" and w["action"] == "watch")
                        text = (f'Available: {result["train"]}\n{result["origin"]} → {result["destination"]}\n'
                            f'{result["date"]} · {result["class"]} · {result["seats"]} seats\n'
                            f'Fare shown: ৳{result["fare"]}\nChecked just now. Availability is not a reservation.\n'
                            + ("Open Rail Sheba to search and buy." if s["mode"] == "away" else "See your desktop dashboard for booking progress."))
                        enqueue(s, text, watch=w)
            s["results"] = s["results"][-1500:]
            s["seen"] = {k: v for k, v in s["seen"].items() if now - v.get("observed", 0) < 86400}
            s["cursor"] += 1
            s["health"].update(status="running", message="Checking your watchlists", last_check=now,
                next_check=self.next_check, checks=s["health"].get("checks", 0) + 1, query=list(query))
        self.store.mutate(save)

    async def telegram_call(self, method, body, *, token=None):
        token = token or self.store.secret("telegram_token", "")
        if not token:
            raise ValueError("Telegram bot is not configured")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"https://api.telegram.org/bot{token}/{method}", json=body)
            data = resp.json()
            if not data.get("ok"):
                raise ValueError("Telegram delivery failed; check the bot and chat settings.")
            return data.get("result")

    async def notifications(self):
        while True:
            try:
                s = self.store.read()
                now = time.time()
                def expire(state):
                    for n in state["outbox"]:
                        if n["status"] == "pending" and n["expires"] < now:
                            n["status"] = "expired"
                        if n["status"] == "pending" and not n.get("test"):
                            watch = next((w for w in state["watches"] if w["id"] == n.get("watch_id")), None)
                            if not state["settings"]["telegram_enabled"] or (n.get("watch_id") and (not watch or not watch["enabled"] or not watch["telegram"])):
                                n["status"] = "cancelled"
                self.store.mutate(expire)
                s = self.store.read()
                item = next((n for n in s["outbox"] if n["status"] == "pending" and n["next"] <= now and n["expires"] > now), None)
                if item:
                    status = "sent"
                    try:
                        chat = self.store.secret("telegram_chat", "")
                        markup = {"inline_keyboard": [[{"text": "Status", "callback_data": "status"},
                            {"text": "Pause monitoring", "callback_data": "pause"}]]}
                        if s["settings"]["public_url"]:
                            markup["inline_keyboard"].insert(0, [{"text": "Open dashboard", "url": s["settings"]["public_url"]}])
                        await self.telegram_call("sendMessage", {"chat_id": chat, "text": item["text"],
                            "disable_notification": in_quiet_hours(s["settings"]), "reply_markup": markup})
                    except Exception:
                        status = "failed" if item["tries"] >= 3 else "pending"
                    def done(state):
                        target = next((n for n in state["outbox"] if n["id"] == item["id"]), None)
                        if target:
                            target.update(status=status, tries=target["tries"] + 1, next=time.time() + 15 * 2 ** target["tries"])
                    self.store.mutate(done)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(2)

    async def telegram_commands(self):
        while True:
            try:
                await asyncio.sleep(1)
                async with self.telegram_lock:
                    if self.store.secret("telegram_token") and self.store.secret("telegram_chat"):
                        if self.telegram_setup and self.telegram_setup["expires"] > time.time() and self.telegram_setup["token"] == self.store.secret("telegram_token"):
                            continue
                        offset = self.store.secret("telegram_offset", 0)
                        updates = await self.telegram_call("getUpdates", {"offset": offset, "timeout": 10,
                            "allowed_updates": ["message", "callback_query"]})
                        for update in updates or []:
                            self.store.set_secret("telegram_offset", update["update_id"] + 1)
                            callback = update.get("callback_query")
                            msg = callback.get("message", {}) if callback else update.get("message", {})
                            sender = callback.get("from", {}) if callback else msg.get("from", {})
                            chat = str(self.store.secret("telegram_chat", ""))
                            # Private chat only: the configured person must also be the sender.
                            if str(msg.get("chat", {}).get("id")) != chat or str(sender.get("id")) != chat:
                                continue
                            command = (callback.get("data", "") if callback else msg.get("text", "").split(" ")[0].lstrip("/")).lower()
                            if command in {"pause", "resume"}:
                                def toggle(s):
                                    s["paused"] = command == "pause"
                                    set_mode(s, "away")
                                self.store.mutate(toggle)
                            elif command == "mute":
                                self.store.mutate(lambda s: s["settings"].update(sound=False))
                            s = self.store.read()
                            reply = f'Mode: {s["mode"]}\nMonitoring: {"paused" if s["paused"] else s["health"]["status"]}\nWatchlists: {len(s["watches"])}\n/status /pause /resume /mute\nResume starts monitoring only.'
                            if callback:
                                await self.telegram_call("answerCallbackQuery", {"callback_query_id": callback["id"], "text": "Updated"})
                            await self.telegram_call("sendMessage", {"chat_id": chat, "text": reply})
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(3)
