from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from cryptography.fernet import Fernet
from .models import Preferences


def initial_state():
    return {"mode": "away", "paused": False, "watches": [], "results": [], "events": [],
            "attempts": [], "outbox": [], "seen": {}, "agent": {"seen": 0, "ready": False},
            "settings": Preferences().model_dump(), "health": {"status": "setup", "checks": 0},
            "commands": [], "cursor": 0}


def event(state, kind, message, **extra):
    item = {"id": secrets.token_hex(6), "at": time.time(), "kind": kind, "message": message, **extra}
    state["events"] = [item] + state["events"][:299]
    return item


class Store:
    """Single-user SQLite store. Each mutation reloads under BEGIN IMMEDIATE.

    A single service process owns scheduling. Durable transactions also prevent
    two simultaneous desktop claims from creating two account-wide attempts.
    """
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        keyfile = self.directory / "encryption.key"
        env_key = os.getenv("RC_ENCRYPTION_KEY")
        if not env_key and not keyfile.exists():
            with keyfile.open("xb") as f:
                f.write(Fernet.generate_key())
            keyfile.chmod(0o600)
        self.cipher = Fernet(env_key.encode() if env_key else keyfile.read_bytes())
        self.db = sqlite3.connect(self.directory / "companion.sqlite", check_same_thread=False, timeout=10)
        self.lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, expires REAL NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO state VALUES (1, ?)", (json.dumps(initial_state()),))
        self.db.commit()

    def read(self):
        with self.lock:
            return json.loads(self.db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])

    def mutate(self, action):
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                state = json.loads(self.db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
                result = action(state)
                self.db.execute("UPDATE state SET body=? WHERE id=1", (json.dumps(state),))
                self.db.commit()
                return result
            except BaseException:
                self.db.rollback()
                raise

    def secret(self, name, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM secrets WHERE name=?", (name,)).fetchone()
            return json.loads(self.cipher.decrypt(row[0].encode())) if row else default

    def set_secret(self, name, value):
        data = self.cipher.encrypt(json.dumps(value).encode()).decode()
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO secrets VALUES (?, ?)", (name, data))

    def create_secret(self, name, value):
        """Atomic first-owner setup, including simultaneous setup requests."""
        data = self.cipher.encrypt(json.dumps(value).encode()).decode()
        with self.lock, self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO secrets VALUES (?, ?)", (name, data))
            return cursor.rowcount == 1

    def session(self, hashed, expires=None):
        with self.lock, self.db:
            if expires is not None:
                self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (hashed, expires))
            else:
                self.db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
                return self.db.execute("SELECT 1 FROM sessions WHERE hash=?", (hashed,)).fetchone() is not None

    def close(self):
        self.db.close()
