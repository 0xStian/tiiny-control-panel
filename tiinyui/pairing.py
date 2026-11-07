"""Phones and other devices: an optional second listener on the local network, open only to paired devices.

Pairing: this computer shows a QR code with a one-time link (valid for 5 minutes). Opening it on a
phone gives that browser a long-lived, HttpOnly session cookie. Only a hash of each session token is
stored, so the file can't be used to log in. Sessions can be revoked at any time. Every API call
from a paired device still needs the per-launch session token (the paired page carries it).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import socket
import threading
import time
from collections import deque
from pathlib import Path

from .common import atomic_write, new_id

CODE_TTL = 300
MAX_FAILURES = 20          # wrong or expired codes per 5 minutes before pairing pauses
COOKIE = "tiiny_session"


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def lan_ip_towards(target: str = "") -> str:
    """This computer's address on the network that leads to `target` (the Tiiny), else the default route."""
    for dest in ([target] if target else []) + ["192.168.0.1", "10.0.0.1"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((dest.split("%")[0], 9))
                ip = s.getsockname()[0]
            finally:
                s.close()
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            continue
    return ""


class Pairing:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.codes = {}
        self.failures = deque(maxlen=200)
        self.sessions = []
        try:
            self.sessions = json.loads(self.path.read_text()).get("sessions", [])
        except (OSError, ValueError):
            self.sessions = []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps({"sessions": self.sessions}, indent=1))

    def new_code(self) -> tuple:
        code = secrets.token_urlsafe(18)
        with self.lock:
            now = time.time()
            self.codes = {k: v for k, v in self.codes.items() if v > now}
            while len(self.codes) >= 5:
                self.codes.pop(next(iter(self.codes)))
            self.codes[_h(code)] = now + CODE_TTL
        return code, CODE_TTL

    def blocked(self) -> bool:
        cutoff = time.time() - 300
        return sum(1 for t in self.failures if t > cutoff) >= MAX_FAILURES

    def redeem(self, code: str, name: str, ip: str):
        """A session token for a valid one-time code, else None."""
        with self.lock:
            if self.blocked():
                return None
            exp = self.codes.pop(_h(code or ""), None)
            if not exp or exp < time.time():
                self.failures.append(time.time())
                return None
            token = secrets.token_urlsafe(32)
            self.sessions.append({"id": new_id("s"), "hash": _h(token), "name": (name or "Device")[:120], "ip": ip,
                                  "created": time.time(), "last_seen": time.time()})
            self._save()
            return token

    def check(self, token: str, ip: str = ""):
        if not token:
            return None
        th = _h(token)
        with self.lock:
            for s in self.sessions:
                if secrets.compare_digest(s["hash"], th):
                    if time.time() - s.get("last_seen", 0) > 60:
                        s["last_seen"], s["ip"] = time.time(), ip or s.get("ip")
                        self._save()
                    return s
        return None

    def revoke(self, sid: str) -> bool:
        with self.lock:
            before = len(self.sessions)
            self.sessions = [s for s in self.sessions if s["id"] != sid]
            self._save()
            return len(self.sessions) != before

    def list(self) -> list:
        with self.lock:
            return [{k: s.get(k) for k in ("id", "name", "ip", "created", "last_seen")} for s in self.sessions]


def device_name(user_agent: str) -> str:
    ua = user_agent or ""
    for needle, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android device"), ("Macintosh", "Mac"),
                         ("Windows", "Windows PC"), ("CrOS", "Chromebook"), ("Linux", "Linux computer")):
        if needle in ua:
            browser = next((b for b in ("Edg", "Firefox", "Chrome", "Safari") if b in ua), "")
            return f"{name}{' · ' + {'Edg': 'Edge'}.get(browser, browser) if browser else ''}"
    return "Device"
