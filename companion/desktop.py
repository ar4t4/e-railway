"""Local-only booking executor. Uses normal Chrome and the owner's real session.

No remote browser debugger is exposed. Every booking mutation checks the
server's current mode, watchlist revision and account-wide attempt lock.
"""
from __future__ import annotations

import base64
import ctypes
import getpass
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import httpx

SITE = "https://eticket.railway.gov.bd"


class NeedsAttention(Exception):
    pass


def normalize(value):
    return " ".join(str(value).casefold().split())


def windows_protect(raw: bytes, decrypt=False):
    """Bind desktop pairing credentials to the current Windows user (DPAPI)."""
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "Railway Companion", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise RuntimeError("Windows could not protect the pairing credentials")
    try:
        return ctypes.string_at(target.data, target.length)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def load_config(root):
    file = root / "agent.dpapi"
    if os.name == "nt" and file.exists():
        return json.loads(windows_protect(file.read_bytes(), decrypt=True))
    if os.getenv("RC_AGENT_TOKEN") and os.getenv("RC_SERVER"):
        return {"url": os.environ["RC_SERVER"].rstrip("/"), "token": os.environ["RC_AGENT_TOKEN"], "id": "desktop-env"}
    return None


def configure(root):
    print("Pair your desktop with Railway Companion. Create a pairing key in Settings first.")
    url = input("Service address [http://127.0.0.1:8787]: ").strip().rstrip("/") or "http://127.0.0.1:8787"
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("Use HTTPS for a remote service, or HTTP for localhost")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use a plain service address")
    token = getpass.getpass("Pairing key (hidden): ").strip()
    config = {"url": url, "token": token, "id": secrets.token_hex(8)}
    with httpx.Client(timeout=10) as client:
        r = client.post(url + "/api/agent/heartbeat", headers={"Authorization": "Bearer " + token},
            json={"id": config["id"], "ready": False, "message": "Paired. Open the railway browser from Settings."})
        if r.status_code != 200:
            raise ValueError("Pairing failed. Check the address and key.")
    if os.name == "nt":
        (root / "agent.dpapi").write_bytes(windows_protect(json.dumps(config).encode()))
    else:
        print("On non-Windows systems use RC_SERVER and RC_AGENT_TOKEN for subsequent starts.")
    return config


class Browser:
    def __init__(self, directory, permit):
        self.directory, self.permit = directory, permit
        self.driver = None
        self.selected = []
        self.disconnected = False

    def release_disconnected_driver(self):
        previous, self.driver = self.driver, None
        self.disconnected = True
        if previous:
            try:
                previous.service.stop()
            except Exception:
                pass

    def connect(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.common.exceptions import SessionNotCreatedException
        if self.driver:
            try:
                _ = self.driver.current_url
                return
            except Exception:
                self.release_disconnected_driver()
        options = webdriver.ChromeOptions()
        preference = self.directory / 'browser.json'
        browser = json.loads(preference.read_text(encoding='utf-8-sig')) if preference.exists() else {}
        binary = browser.get('binary')
        if binary:
            if not Path(binary).is_file():
                raise NeedsAttention('The selected browser is not installed. Run Start-Desktop.ps1 -Browser Chrome or -Browser Brave to choose again.')
            options.binary_location = binary
        profile = 'railway-profile-brave' if browser.get('name') == 'Brave' else 'railway-profile'
        options.add_argument(f"--user-data-dir={self.directory / profile}")
        options.add_argument("--start-maximized")
        options.add_experimental_option("detach", True)
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        os.environ.setdefault('SE_TIMEOUT', '30')
        try:
            cached_driver = Path(__file__).resolve().parent.parent / '.runtime' / 'chromedriver' / 'chromedriver.exe'
            if cached_driver.is_file():
                try:
                    self.driver = webdriver.Chrome(service=Service(str(cached_driver)), options=options)
                except SessionNotCreatedException:
                    # Browser updates may outgrow the bundled driver.
                    self.driver = webdriver.Chrome(options=options)
            else:
                self.driver = webdriver.Chrome(options=options)
        except Exception:
            raise NeedsAttention('Could not open the railway browser. Check your internet connection and close any old Railway browser window, then click Connect Railway again.') from None
        self.disconnected = False
        self.driver.set_page_load_timeout(45)
        self.driver.get(SITE)
        if not self.session():
            self.driver.get(SITE + "/login")

    def session(self):
        from selenium.common.exceptions import WebDriverException
        if not self.driver:
            return None
        try:
            if urlparse(self.driver.current_url).hostname != "eticket.railway.gov.bd":
                return None
            data = self.driver.execute_script("return {token:localStorage.token, uudid:localStorage.uudid, ssdk:localStorage.ssdk, user_agent:navigator.userAgent}")
        except WebDriverException:
            self.release_disconnected_driver()
            return None
        if not data.get("token") or not data.get("uudid"):
            return None
        try:
            middle = data["token"].split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
            if claims.get("exp", 0) < time.time() + 30:
                return None
        except Exception:
            pass
        return data

    def visible(self, selector):
        from selenium.webdriver.common.by import By
        return [e for e in self.driver.find_elements(By.CSS_SELECTOR, selector) if e.is_displayed()]

    def wait_visible(self, selector, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            items = self.visible(selector)
            if items:
                return items[0]
            if self.visible(".confirm-ticket-otp input, confirm-ticket-otp input"):
                raise NeedsAttention("Verification required. Complete it in your railway browser.")
            time.sleep(.3)
        raise NeedsAttention("The expected booking step did not appear. Check your railway browser.")

    def check(self):
        if not self.permit():
            raise NeedsAttention("Automation paused or disconnected. Check any existing selection in your browser.")
        if urlparse(self.driver.current_url).hostname != "eticket.railway.gov.bd":
            raise NeedsAttention("The browser left the railway website. Continue manually.")

    def click(self, element):
        self.check()
        element.click()  # Never retry an uncertain mutation blindly.

    def browser_route(self, result, timeout=35):
        """Verify the SPA's own response belongs to the precise journey/class."""
        expected = {"from_city": result["origin"], "to_city": result["destination"],
                    "date_of_journey": date.fromisoformat(result["date"]).strftime("%d-%b-%Y")}
        pending = set()
        end = time.time() + timeout
        while time.time() < end:
            for record in self.driver.get_log("performance"):
                msg = json.loads(record["message"])["message"]
                if msg["method"] != "Network.responseReceived":
                    continue
                params = msg["params"]
                url = params.get("response", {}).get("url", "")
                if "bookings/search-trips-v2" not in url:
                    continue
                q = parse_qs(urlparse(url).query)
                if all(q.get(k, [""])[0] == v for k, v in expected.items()):
                    pending.add(params["requestId"])
            for request_id in list(pending):
                try:
                    body = self.driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
                    payload = json.loads(base64.b64decode(body["body"]) if body.get("base64Encoded") else body["body"])
                except Exception:
                    continue
                pending.remove(request_id)
                for train in payload.get("data", {}).get("trains", []):
                    name = train.get("trip_number") or train.get("train_name")
                    if normalize(name) != normalize(result["train"]):
                        continue
                    for st in train.get("seat_types", []):
                        if st.get("type") == result["class"]:
                            route = str(st.get("trip_route_id") or train.get("trip_route_id") or "")
                            if route:
                                return route
            time.sleep(.4)
        raise NeedsAttention("Could not verify the exact journey in the browser. No seats were selected.")

    def choose_card(self, train_name):
        cards = self.visible(".trip-collapsible")
        number = re.search(r"\((\d+)\)", train_name)
        name = normalize(re.sub(r"\(\d+\)", "", train_name))
        matches = [c for c in cards if name in normalize(c.text) or
                   (number and re.search(r"(?<!\d)" + number[1] + r"(?!\d)", c.text))]
        if len(matches) != 1:
            raise NeedsAttention("Could not uniquely verify the train card. No seats were selected.")
        return matches[0]

    def switch_coach(self, value):
        from selenium.webdriver.support.ui import Select
        self.check()
        pickers = self.visible("app-seat-layout select.form-control")
        if pickers:
            Select(pickers[0]).select_by_value(value)
            time.sleep(.45)

    def read_grid(self, coach):
        from selenium.webdriver.common.by import By
        output = []
        for wrapper in self.driver.find_elements(By.CSS_SELECTOR, "div.seat-wrapper[ticketid][routeid]"):
            buttons = wrapper.find_elements(By.CSS_SELECTOR, "button.btn-seat")
            if not buttons or not buttons[0].is_displayed():
                continue
            b = buttons[0]
            classes = set((b.get_attribute("class") or "").split())
            state = next((s for s, c in [("selected", "seat-selected"), ("held", "seat-in-progress"), ("booked", "seat-booked"), ("available", "seat-available")] if c in classes), "unknown")
            output.append({"id": wrapper.get_attribute("ticketid"), "route": wrapper.get_attribute("routeid"),
                "seat": b.text.strip(), "coach": coach, "state": state, "button": b})
        return output

    def book(self, job):
        from selenium.webdriver.common.by import By
        self.selected = []
        result = job["result"]
        if not self.session():
            raise NeedsAttention("Sign in to the railway in your desktop browser first.")
        self.check()
        self.driver.get_log("performance")
        self.driver.get(result["url"])
        route_id = self.browser_route(result)
        self.wait_visible(".trip-collapsible")
        card = self.choose_card(result["train"])
        rows = card.find_elements(By.CSS_SELECTOR, ".single-seat-class")
        rows = [r for r in rows if normalize(r.text).split(" ")[0] == result["class"].casefold()]
        if len(rows) != 1:
            raise NeedsAttention("The requested class could not be verified. No seats were selected.")
        buttons = rows[0].find_elements(By.CSS_SELECTOR, "button.book-now-btn")
        if len(buttons) != 1:
            raise NeedsAttention("The railway class controls changed. Please review the browser.")
        self.click(buttons[0])
        self.wait_visible(".seat-layout-view", 30)
        pickers = self.visible("app-seat-layout select.form-control")
        coaches = [o.get_attribute("value") for o in pickers[0].find_elements(By.TAG_NAME, "option")
                   if o.get_attribute("value") and not o.get_attribute("disabled")] if pickers else [""]
        available = []
        for coach in coaches:
            self.switch_coach(coach)
            available.extend([{k: v for k, v in s.items() if k != "button"} for s in self.read_grid(coach)
                              if s["state"] == "available" and s["route"] == route_id and s["seat"]])
        # Same-coach grouping when possible, without making it a requirement.
        available = list({s["id"]: s for s in available}.values())
        count = job["passengers"]
        if len(available) < count:
            raise NeedsAttention("Not enough selectable seats remain for your whole group. No seats were selected.")
        chosen = next(([s for s in available if s["coach"] == c][:count] for c in coaches
                       if sum(s["coach"] == c for s in available) >= count), available[:count])
        for seat in chosen:
            self.switch_coach(seat["coach"])
            current = next((s for s in self.read_grid(seat["coach"]) if s["id"] == seat["id"] and s["route"] == route_id), None)
            if not current or current["state"] != "available":
                raise NeedsAttention("Availability changed during selection. Review the current seats before retrying.")
            self.click(current["button"])
            end = time.time() + 8
            while time.time() < end:
                current = next((s for s in self.read_grid(seat["coach"]) if s["id"] == seat["id"]), None)
                if current and current["state"] == "selected":
                    self.selected.append(seat["seat"])
                    break
                time.sleep(.3)
            else:
                raise NeedsAttention("A seat click could not be confirmed as your selection. Check the browser before retrying.")
        for seat in chosen:
            self.switch_coach(seat["coach"])
            if not any(s["id"] == seat["id"] and s["route"] == route_id and s["state"] == "selected" for s in self.read_grid(seat["coach"])):
                raise NeedsAttention("The complete group selection could not be verified. Check the browser.")
        if job["action"] == "seats":
            return "seats_selected", "Your seats are selected. Continue in the railway browser."
        self.click(self.wait_visible("#confirmbooking .continue-btn"))
        self.wait_visible("#pname0", 35)
        passengers = job.get("passenger_details", [])
        if len(passengers) != count:
            raise NeedsAttention("Seats selected. Fill the passenger details in your railway browser.")
        for idx, person in enumerate(passengers):
            field = self.wait_visible(f"#pname{idx}", 3)
            self.check()
            field.clear()
            field.send_keys(person["name"])
        for selector, key in (("#pmobile", "mobile"), ("#pemail", "email")):
            fields = self.visible(selector)
            if fields and passengers[0].get(key):
                self.check()
                fields[0].clear()
                fields[0].send_keys(passengers[0][key])
        invalid = self.driver.execute_script("return [...document.querySelectorAll('input:invalid, select:invalid')].some(e=>e.offsetParent!==null)")
        if invalid:
            raise NeedsAttention("Additional passenger details are required. Complete them in your browser.")
        submit = self.wait_visible("#confirm_button", 5)
        if not submit.is_enabled():
            raise NeedsAttention("Passenger confirmation needs your attention in the browser.")
        self.click(submit)
        # Only a specific, visible payment method control establishes readiness.
        self.wait_visible("#bKash_button", 30)
        return "payment_ready", "Payment options are ready in your railway browser. Complete payment there."


class DesktopAgent:
    def __init__(self, config, directory):
        self.config, self.directory = config, directory
        self.commands = queue.Queue()
        self.ready = False
        self.message = "Desktop connected. Click Connect Railway to sign in automatically when your saved session is valid."
        self.current_job = None
        self.mode = "away"
        self.sound = True
        self.connected = False
        self.running = True
        self.tray = None
        self.browser = Browser(directory, self.permit)
        self.synced_session = None

    def sync_browser_session(self, force=False):
        session = self.browser.session()
        self.ready = bool(session)
        if not session:
            self.synced_session = None
            return
        if force or session != self.synced_session:
            self.call('session', session)
            self.synced_session = dict(session)
            self.message = 'Railway connected. Session synced automatically; monitoring can now check your watchlists.'

    def call(self, path, body):
        with httpx.Client(timeout=12) as client:
            r = client.post(self.config["url"] + "/api/agent/" + path, json=body,
                headers={"Authorization": "Bearer " + self.config["token"]})
            if r.status_code != 200:
                raise RuntimeError("Service rejected the desktop request. Check pairing and connection.")
            return r.json()

    def permit(self):
        if not self.running or not self.current_job or not self.connected or self.mode != "at_pc":
            return False
        try:
            return self.call("permit/" + self.current_job["id"], {"id": self.config["id"]})["allowed"]
        except Exception:
            return False

    def heartbeat(self):
        while self.running:
            try:
                response = self.call("heartbeat", {"id": self.config["id"], "ready": self.ready, "message": self.message})
                self.mode, self.sound, self.connected = response["mode"], response["sound"], True
                for command in response["commands"]:
                    self.commands.put(command)
            except Exception:
                self.connected = False
                self.mode = "away"
            time.sleep(4)

    def alarm(self, job):
        if self.tray and self.mode == "at_pc":
            try:
                self.tray.notify(self.message, "Railway Companion · your booking")
            except Exception:
                pass
        if os.name == "nt" and job["sound"] and self.sound and self.mode == "at_pc":
            import winsound
            for _ in range(3):
                winsound.Beep(1000, 220)
                time.sleep(.15)

    def start_tray(self):
        if os.name != "nt":
            return
        import webbrowser
        import pystray
        from PIL import Image, ImageDraw
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, 62, 62), radius=16, fill="#d9f1a7")
        draw.rounded_rectangle((15, 10, 49, 48), radius=7, fill="#163c32")
        draw.rectangle((21, 17, 43, 32), fill="#d9f1a7")
        draw.ellipse((21, 38, 26, 43), fill="#d9f1a7")
        draw.ellipse((38, 38, 43, 43), fill="#d9f1a7")
        draw.line((24, 48, 18, 55), fill="#163c32", width=4)
        draw.line((40, 48, 46, 55), fill="#163c32", width=4)

        def open_dashboard(icon, item):
            webbrowser.open(self.config["url"])

        def connect(icon, item):
            self.commands.put("connect")

        def sync(icon, item):
            self.commands.put("sync_session")

        def away(icon, item):
            self.mode = "away"
            try:
                self.call("away", {})
            except Exception:
                pass

        def stop(icon, item):
            self.running = False
            away(icon, item)
            icon.stop()

        self.tray = pystray.Icon("railway-companion", image, "Railway Companion", pystray.Menu(
            pystray.MenuItem("Open dashboard", open_dashboard, default=True),
            pystray.MenuItem("Open railway browser", connect),
            pystray.MenuItem("Sync monitoring session", sync),
            pystray.MenuItem("Switch to Away mode", away),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit desktop agent (monitor keeps running)", stop)))
        threading.Thread(target=self.tray.run, daemon=True).start()

    def persist_report(self, job, status, message):
        pending = {"attempt": job["id"], "body": {"id": self.config["id"], "status": status,
                   "message": message, "selected": list(self.browser.selected)}}
        # No credentials/passenger names here. Persist before sending: a network
        # failure must not erase evidence that a seat may have been selected.
        file = self.directory / "pending-report.json"
        file.write_text(json.dumps(pending), encoding="utf-8")
        self.flush_report()

    def flush_report(self):
        file = self.directory / "pending-report.json"
        if not file.exists():
            return
        value = json.loads(file.read_text(encoding="utf-8"))
        self.call("report/" + value["attempt"], value["body"])
        file.unlink()

    def run(self):
        self.start_tray()
        threading.Thread(target=self.heartbeat, daemon=True).start()
        print("Desktop agent running. Open Settings in your dashboard to connect the railway browser.")
        while self.running:
            try:
                self.flush_report()
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    command = None
                if command == "connect":
                    self.message = "Opening railway browser..."
                    self.browser.connect()
                    self.synced_session = None
                    self.message = "Sign in in the railway window. Your session will connect automatically."
                if command == "sync_session":
                    self.synced_session = None
                self.sync_browser_session()
                if self.browser.disconnected:
                    self.message = "Railway browser closed or disconnected. Click Connect Railway to reopen it."
                if self.ready and self.connected and self.mode == "at_pc":
                    job = self.call("claim", {"id": self.config["id"]}).get("job")
                    if job:
                        self.current_job = job
                        self.message = "Preparing " + job["result"]["train"]
                        try:
                            status, msg = self.browser.book(job)
                        except NeedsAttention as exc:
                            status, msg = "needs_attention", str(exc)
                        except Exception:
                            status, msg = "reconcile", "Browser action was interrupted. Check the official browser before retrying."
                        self.message = msg
                        self.persist_report(job, status, msg)
                        self.alarm(job)
                        self.current_job = None
            except KeyboardInterrupt:
                self.running = False
            except NeedsAttention as exc:
                self.message = str(exc)
            except Exception:
                self.ready = False
                self.message = "Connection or browser needs attention. Check your dashboard; retrying."
            time.sleep(2)


def main():
    root = Path(os.getenv("RC_DESKTOP_DIR", str(Path(__file__).resolve().parent.parent / "data" / "desktop")))
    root.mkdir(parents=True, exist_ok=True)
    # Keep one local executor per profile, including repeated launcher clicks.
    lock = (root / "agent.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            if lock.read(1) == b"":
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("The desktop agent is already running. Stop it before pairing again.")
        return
    if "--local" in sys.argv:
        # Only the explicit local launcher may repair credentials from another
        # Windows account. Remote pairing must never be silently replaced.
        from .local_desktop import pair_local
        try:
            config = load_config(root)
        except (RuntimeError, ValueError):
            config = None
        local_url = os.getenv('RC_LOCAL_URL', 'http://127.0.0.1:8787')
        if config and config['url'].rstrip('/') == local_url.rstrip('/'):
            try:
                DesktopAgent(config, root).call('heartbeat', {'id': config['id'], 'ready': False,
                    'message': 'Desktop connected. Click Connect Railway.'})
            except RuntimeError:
                config = None
        if not config:
            config = pair_local(root, os.getenv('RC_DATA_DIR'), local_url)
    elif "--pair-local" in sys.argv:
        from .local_desktop import pair_local
        config = pair_local(root)
        print("Desktop paired with this PC. Credentials are protected by Windows.")
    else:
        config = None if "--pair" in sys.argv else load_config(root)
    if not config:
        config = configure(root)
    if "--pair-only" not in sys.argv:
        (root / 'agent.pid').write_text(str(os.getpid()), encoding='ascii')
        DesktopAgent(config, root).run()


if __name__ == "__main__":
    main()
