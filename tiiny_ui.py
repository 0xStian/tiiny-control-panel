#!/usr/bin/env python3
"""Tiiny Control Panel: a local web interface for your Tiiny device.

Usage:
    python3 tiiny_ui.py                # serve on http://127.0.0.1:8765 and open a browser
    python3 tiiny_ui.py --no-browser   # serve only
    python3 tiiny_ui.py --help

Keep these together when you move the panel: tiiny_ui.py, the tiinyui/ folder and static/.

How it works:
  * The device address and API key come from the official tiiny CLI's config
    (~/.local/tiiny/config.json, written by `tiiny connect` and `tiiny login`).
    They can be overridden in the page under System → Panel settings.
  * Data and actions go straight to the device's HTTP API (the endpoints documented at
    https://dev.tiiny.ai); interactive CLI flows run the real `tiiny` binary in a terminal.
  * Chats, projects and other saved work live in ~/.local/share/tiiny-ui/.

Security: the server listens on 127.0.0.1, checks the Host and Origin headers, and every API
call needs a session token made at start-up. On this computer, only a browser the panel signed in
(with a one-time link) can get that token; see tiinyui/browserauth.py. The tiiny program it runs is
fixed at start-up (--tiiny), and agent commands only exist with --allow-commands (tiinyui/commands.py),
which the page can't switch on. The API key stays on this computer; the browser never sees it unless
you press Reveal. The optional phone listener (System → Phone and other devices) only serves
devices paired by QR code, and they get a safer subset of the API (LAN_FORBIDDEN).

Standard library only (Python 3.8+, Linux/macOS).
"""

from __future__ import annotations

import argparse
import base64
import codecs
import hmac
import http.client
import http.cookies
import json
import os
import re
import secrets
import select
import shlex
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from html import escape as html_escape
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":  # the in-page terminal needs a Unix pseudo-terminal
    sys.exit("Tiiny Control Panel runs on Linux and macOS. Windows isn't supported yet.")

from tiinyui import VERSION, activity, backup, commands, decide, firmware, history, launcher, selfupdate, webfetch, websearch
from tiinyui.browserauth import CODE_TTL, STORAGE_KEY, BrowserAuth
from tiinyui.clitools import Runtime
from tiinyui.common import BORROWED, USAGE, BadRequest, DeviceError, clamp_int, data_dir, error_message, guess_mime, mask, safe_name, valid_id
from tiinyui.device import (FORWARD_HEADERS, MAX_DEVICE_RESPONSE, SERVICES, DeviceConn, build_path, call as device_call,
                            device_json, device_open, discover_devices, multipart_body)
from tiinyui.device import vault_upload as device_vault_upload
from tiinyui.jobs import JobManager
from tiinyui.projects import Projects
from tiinyui.review import Reviews
from tiinyui.studio import Gallery, Studio
from tiinyui.meetings import Meetings
from tiinyui.metrics import Metrics
from tiinyui.pairing import COOKIE, Pairing, device_name, lan_ip_towards
from tiinyui.pty import PtyManager
from tiinyui.settings import Settings
from tiinyui.store import Store

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "tiiny-ui"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
INSTALL_CMD = 'curl -fsSL "https://tiiny-cli.tiiny.cloud/install-linux.sh" | bash && source ~/.profile'

MAX_JSON_BODY = 64 * 1024 * 1024        # chat and OCR requests carry images
MAX_UPLOAD = 4 * 1024 ** 3

SETTINGS: Settings
RT: Runtime
DEVCONN: DeviceConn
STORE: Store
PROJECTS: Projects
REVIEWS: Reviews
GALLERY: Gallery
STUDIO: Studio
MEETINGS: Meetings
METRICS: Metrics
PAIRING: Pairing
BROWSER: BrowserAuth
HTTPD = None
LAN = {"server": None, "ip": "", "port": 0, "error": ""}
LAN_LOCK = threading.Lock()
TLS = {}
JOBS = JobManager()
PTYS = PtyManager()
TOKEN = secrets.token_urlsafe(32)
VERBOSE = False
POOL = ThreadPoolExecutor(max_workers=8)

CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' blob: data:; connect-src 'self' blob: data:; font-src 'self' data:; worker-src 'self' blob:; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
FILE_CSP = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"
RISKY_MIME = ("text/html", "image/svg+xml", "application/xhtml+xml", "text/xml", "application/xml")
# What paired phones may not do: change the panel, browse or read this computer's files, run the tiiny CLI
# (it takes file paths), see the API key. They control the device and use the panel's features.
LAN_FORBIDDEN = ("/api/lan", "/api/shutdown", "/api/launcher", "/api/settings", "/api/device/key", "/api/fs/",
                 "/api/cli", "/api/pty", "/api/session", "/api/commands", "/api/files/", "/api/update", "/api/backup")
# Settings that decide which program the panel runs; only changeable when starting it (--tiiny, --cli-config).
STARTUP_ONLY_SETTINGS = ("tiiny_bin", "cli_config")
START_ARGS = []  # the start-up options to repeat in the app-menu entry (--tiiny, --cli-config, --port, TLS)
RESTART = {"wanted": False}  # /api/update/restart: once the server has stopped, start again (with the new version)
ALLOW_COMMANDS = False  # --allow-commands: agent mode may run commands and read and write files (see tiinyui/commands.py)
# Endpoints that exist only for one of agent mode's tools: they refuse when you switch that tool off under
# System → Agent tools (agent_tools_off). The switches can only narrow what's allowed (commands still need --allow-commands).
TOOL_OF_ENDPOINT = {"api_history_search": "history", "api_history_read": "history", "api_command_run": "run_command",
                    "api_file_read": "read_file", "api_file_write": "write_file", "api_file_find": "find_files",
                    "api_file_open": "open_on_computer"}
COMMANDS_OFF = ("Commands are switched off. To let agent mode run commands and read and write files on this computer, "
                "start the panel with python3 tiiny_ui.py --allow-commands. It still asks before every command and file change.")


class Handler(BaseHTTPRequestHandler):
    server_version = "TiinyUI/" + VERSION
    protocol_version = "HTTP/1.1"

    ROUTES = [
        ("GET", r"/api/health", "api_health"),
        ("POST", r"/api/settings", "api_settings"),
        ("GET", r"/api/device/key", "api_device_key"),
        ("GET", r"/api/overview", "api_overview"),
        ("POST", r"/api/decide", "api_decide"),
        ("GET", r"/api/firmware", "api_firmware"),
        ("POST", r"/api/firmware/(check|install|cancel|reboot)", "api_firmware_action"),
        ("GET", r"/api/discover", "api_discover"),
        ("POST", r"/api/dev", "api_dev"),
        ("POST", r"/api/dev/stream", "api_dev_stream"),
        ("GET", r"/api/dev/icon", "api_dev_icon"),
        ("POST", r"/api/chat", "api_chat"),
        ("POST", r"/api/cli", "api_cli"),
        ("GET", r"/api/pty", "api_pty_list"),
        ("POST", r"/api/pty", "api_pty_start"),
        ("GET", r"/api/pty/([\w-]+)/stream", "api_pty_stream"),
        ("POST", r"/api/pty/([\w-]+)/input", "api_pty_input"),
        ("POST", r"/api/pty/([\w-]+)/resize", "api_pty_resize"),
        ("POST", r"/api/pty/([\w-]+)/kill", "api_pty_kill"),
        ("PUT", r"/api/upload", "api_upload"),
        ("GET", r"/api/file/([\w-]+)", "api_file"),
        # data store (chats, ...)
        ("POST", r"/api/store/chats/import", "api_store_import"),
        ("GET", r"/api/store/([a-z]+)", "api_store_list"),
        ("POST", r"/api/store/([a-z]+)", "api_store_create"),
        ("GET", r"/api/store/([a-z]+)/([\w-]+)", "api_store_get"),
        ("PUT", r"/api/store/([a-z]+)/([\w-]+)", "api_store_put"),
        ("DELETE", r"/api/store/([a-z]+)/([\w-]+)", "api_store_delete"),
        # background jobs
        ("GET", r"/api/jobs", "api_jobs"),
        ("GET", r"/api/jobs/([\w-]+)", "api_job"),
        ("GET", r"/api/jobs/([\w-]+)/stream", "api_job_stream"),
        ("POST", r"/api/jobs/([\w-]+)/cancel", "api_job_cancel"),
        # this computer's files, the web
        ("GET", r"/api/fs/list", "api_fs_list"),
        ("POST", r"/api/web/fetch", "api_web_fetch"),
        ("POST", r"/api/web/search", "api_web_search"),
        ("POST", r"/api/commands", "api_command_run"),
        ("POST", r"/api/files/read", "api_file_read"),
        ("POST", r"/api/files/write", "api_file_write"),
        ("POST", r"/api/files/find", "api_file_find"),
        ("POST", r"/api/files/open", "api_file_open"),
        ("POST", r"/api/history/search", "api_history_search"),
        ("POST", r"/api/history/read", "api_history_read"),
        ("POST", r"/api/web/to-vault", "api_web_to_vault"),
        # projects
        ("GET", r"/api/projects", "api_projects"),
        ("POST", r"/api/projects", "api_project_create"),
        ("GET", r"/api/projects/([\w-]+)", "api_project_get"),
        ("PATCH", r"/api/projects/([\w-]+)", "api_project_update"),
        ("DELETE", r"/api/projects/([\w-]+)", "api_project_delete"),
        ("POST", r"/api/projects/([\w-]+)/docs", "api_project_add_doc"),
        ("GET", r"/api/projects/([\w-]+)/docs/([\w-]+)", "api_project_doc"),
        ("DELETE", r"/api/projects/([\w-]+)/docs/([\w-]+)", "api_project_remove_doc"),
        ("POST", r"/api/projects/([\w-]+)/index", "api_project_index"),
        ("POST", r"/api/projects/([\w-]+)/context", "api_project_context"),
        ("POST", r"/api/projects/([\w-]+)/search", "api_project_search"),
        ("POST", r"/api/projects/([\w-]+)/memory", "api_project_memory"),
        ("POST", r"/api/projects/([\w-]+)/read", "api_project_read"),
        # gallery and studio
        ("GET", r"/api/gallery", "api_gallery"),
        ("POST", r"/api/gallery", "api_gallery_add"),
        ("POST", r"/api/gallery/delete", "api_gallery_delete_many"),
        ("POST", r"/api/gallery/favorite", "api_gallery_favorite_many"),
        ("POST", r"/api/gallery/tag", "api_gallery_tag"),
        ("GET", r"/api/gallery/([\w-]+)", "api_gallery_get"),
        ("PATCH", r"/api/gallery/([\w-]+)", "api_gallery_update"),
        ("DELETE", r"/api/gallery/([\w-]+)", "api_gallery_delete"),
        ("GET", r"/api/gallery/([\w-]+)/files/([^/]+)", "api_gallery_file"),
        ("POST", r"/api/studio/(music|3d|video)", "api_studio_start"),
        ("POST", r"/api/jobs/([\w-]+)/control", "api_job_control"),
        # phones and other devices
        ("GET", r"/api/lan", "api_lan"),
        ("POST", r"/api/lan", "api_lan_set"),
        ("POST", r"/api/lan/pair", "api_lan_pair"),
        ("DELETE", r"/api/lan/devices/([\w-]+)", "api_lan_revoke"),
        # app menu entry and stopping the panel
        ("GET", r"/api/launcher", "api_launcher"),
        ("POST", r"/api/launcher", "api_launcher_set"),
        ("POST", r"/api/shutdown", "api_shutdown"),
        # updating the panel, and backing up its own data (this computer only: both are in LAN_FORBIDDEN)
        ("GET", r"/api/update", "api_update"),
        ("POST", r"/api/update/check", "api_update_check"),
        ("POST", r"/api/update/install", "api_update_install"),
        ("POST", r"/api/update/restart", "api_update_restart"),
        ("GET", r"/api/backup", "api_backup"),
        ("POST", r"/api/backup/export", "api_backup_export"),
        ("POST", r"/api/backup/inspect", "api_backup_inspect"),
        ("POST", r"/api/backup/restore", "api_backup_restore"),
        # performance history and the idle watcher
        ("GET", r"/api/metrics", "api_metrics"),
        ("GET", r"/api/metrics/speed", "api_metrics_speed"),
        ("GET", r"/api/metrics/idle", "api_metrics_idle"),
        ("POST", r"/api/models/borrowed", "api_models_borrowed"),
        # meetings
        ("GET", r"/api/meetings", "api_meetings"),
        ("POST", r"/api/meetings", "api_meeting_create"),
        ("GET", r"/api/meetings/([\w-]+)", "api_meeting_get"),
        ("PATCH", r"/api/meetings/([\w-]+)", "api_meeting_update"),
        ("DELETE", r"/api/meetings/([\w-]+)", "api_meeting_delete"),
        ("POST", r"/api/meetings/([\w-]+)/segments", "api_meeting_segments"),
        ("POST", r"/api/meetings/([\w-]+)/summarize", "api_meeting_summarize"),
        ("POST", r"/api/meetings/([\w-]+)/highlights", "api_meeting_highlights"),
        ("POST", r"/api/meetings/([\w-]+)/to-vault", "api_meeting_to_vault"),
        ("POST", r"/api/meetings/([\w-]+)/to-project", "api_meeting_to_project"),
        ("GET", r"/api/meetings/([\w-]+)/export", "api_meeting_export"),
        # code review
        ("GET", r"/api/reviews", "api_reviews"),
        ("POST", r"/api/reviews", "api_review_create"),
        ("GET", r"/api/reviews/([\w-]+)", "api_review_get"),
        ("DELETE", r"/api/reviews/([\w-]+)", "api_review_delete"),
        ("POST", r"/api/reviews/([\w-]+)/start", "api_review_start"),
        ("POST", r"/api/reviews/([\w-]+)/select", "api_review_select"),
        ("PATCH", r"/api/reviews/([\w-]+)/findings/([\w-]+)", "api_review_mark"),
        ("GET", r"/api/reviews/([\w-]+)/code", "api_review_code"),
        ("GET", r"/api/reviews/([\w-]+)/export", "api_review_export"),
    ]
    COMPILED = [(m, re.compile(p + r"$"), name) for m, p, name in ROUTES]

    # -- plumbing -------------------------------------------------------------------
    def log_message(self, fmt, *args):
        if VERBOSE:
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def send_response(self, *args, **kwargs):
        self._responded = True
        super().send_response(*args, **kwargs)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def allowed_hosts(self):
        if getattr(self.server, "lan", False):
            return {self.server.lan_host}
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if port == 80:
            hosts |= {"127.0.0.1", "localhost", "[::1]"}
        return hosts

    def dispatch(self, method):
        self._responded = False
        self._sse = False
        try:
            host = (self.headers.get("Host") or "").strip().lower()
            if self.server.check_host and host not in self.allowed_hosts():
                self.close_connection = True
                return self.send_text(403, "Forbidden host")
            url = urllib.parse.urlsplit(self.path)
            path, query = url.path, urllib.parse.parse_qs(url.query)
            if self.is_remote():  # phones and other paired devices
                if method == "GET" and path == "/pair":
                    return self.lan_pair(query)
                if not PAIRING.check(self.cookie(COOKIE), self.client_address[0]):
                    self.close_connection = True
                    if method == "GET" and path in ("/", "/index.html"):
                        return self.lan_page(401, "This device isn’t paired",
                                             "Open the Tiiny Control Panel on your computer, go to System → Phone and other devices, "
                                             "and scan the pairing code with this device.")
                    return self.send_json(401, {"error": "This device isn't paired with the Tiiny Control Panel any more."})
                if path.startswith(LAN_FORBIDDEN):
                    self.close_connection = True
                    return self.send_json(403, {"error": "For safety, that only works on the computer running the panel."})
            else:  # this computer: 127.0.0.1 is open to every program and account on it, so the session token
                # only goes to a browser the panel signed in (see tiinyui/browserauth.py)
                if method == "GET" and path == "/auth":
                    return self.local_auth(query)
                if method == "POST" and path == "/api/session":
                    return self.api_session()
            if method == "GET" and path in ("/", "/index.html"):
                return self.serve_index()
            if method == "GET" and path.startswith("/static/"):
                return self.serve_static(path[len("/static/"):])
            if method == "GET" and path == "/sw.js":
                return self.serve_sw()
            if method == "GET" and path == "/__tiiny__":  # lets a second start find this one (and check it's really ours)
                info = {"app": "tiiny-ui", "version": VERSION}
                challenge = query.get("challenge", [""])[0]
                if challenge and not self.is_remote():
                    info["proof"] = BROWSER.proof(challenge)
                return self.send_json(200, info)
            if not path.startswith("/api/"):
                return self.send_text(404, "Not found")
            token = self.headers.get("X-Tiiny-Token") or query.get("token", [""])[0]
            if not secrets.compare_digest(token.encode(), TOKEN.encode()):
                self.close_connection = True
                return self.send_json(403, {"error": "Session expired. Reload the page.", "session": "expired"})
            origin = self.headers.get("Origin")
            if method != "GET" and origin and self.server.check_host and \
                    urllib.parse.urlsplit(origin).netloc.lower() not in self.allowed_hosts():
                self.close_connection = True
                return self.send_json(403, {"error": "Cross-origin request refused"})
            for m, rx, name in self.COMPILED:
                if m == method:
                    match = rx.match(path)
                    if match:
                        if name in TOOL_OF_ENDPOINT and TOOL_OF_ENDPOINT[name] in (SETTINGS.get("agent_tools_off") or []):
                            raise BadRequest("That agent tool is switched off in System → Agent tools.")
                        return getattr(self, name)(*match.groups(), q=query)
            self.close_connection = True
            return self.send_json(404, {"error": "Unknown endpoint"})
        except BadRequest as e:
            self.close_connection = True
            if not self._responded:
                self.send_json(400, {"error": str(e)})
            elif self._sse:
                self.sse_safe("error", {"message": str(e)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001
            self.close_connection = True
            traceback.print_exc()
            if not self._responded:
                self.send_json(500, {"error": f"Internal error: {e}"})
            elif self._sse:
                self.sse_safe("error", {"message": f"Internal error: {e}"})

    def send_bytes(self, status, data: bytes, ctype: str, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        extra = dict(extra or {})
        extra.setdefault("Cache-Control", "no-store")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, obj):
        self.send_bytes(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def send_text(self, status, text):
        self.send_bytes(status, text.encode(), "text/plain; charset=utf-8")

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_JSON_BODY:
            self.close_connection = True
            raise BadRequest("Request body is too large")
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise BadRequest("Invalid JSON body")
        if not isinstance(data, dict):
            raise BadRequest("JSON body must be an object")
        return data

    def sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self._sse = True

    def sse(self, event, data):
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode())

    def sse_safe(self, event, data):
        try:
            self.sse(event, data)
        except OSError:
            pass

    # -- static -----------------------------------------------------------------------
    def serve_index(self):
        try:
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            return self.send_text(500, "static/index.html is missing")
        html = html.replace("{{TOKEN}}", TOKEN if self.is_remote() else "")
        self.send_bytes(200, html.encode(), "text/html; charset=utf-8",
                        {"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer"})

    def cookie(self, name):
        try:
            jar = http.cookies.SimpleCookie(self.headers.get("Cookie") or "")
        except http.cookies.CookieError:
            return ""
        return jar[name].value if name in jar else ""

    def lan_page(self, status, title, text, extra_headers=None, refresh=None):
        esc = html_escape
        body = (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                + (f'<meta http-equiv="refresh" content="1;url={esc(refresh)}">' if refresh else "")
                + f'<title>{esc(title)}</title><body style="font:16px/1.5 system-ui,sans-serif;max-width:32em;margin:14vh auto;padding:0 20px">'
                f'<h1 style="font-size:22px">{esc(title)}</h1><p>{esc(text)}</p></body>')
        headers = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'", "Referrer-Policy": "no-referrer"}
        headers.update(extra_headers or {})
        self.send_bytes(status, body.encode(), "text/html; charset=utf-8", headers)

    def lan_pair(self, query):
        if PAIRING.blocked():
            self.close_connection = True
            return self.lan_page(429, "Too many attempts", "Pairing is paused for a few minutes. Make a new code on your computer afterwards.")
        token = PAIRING.redeem(query.get("code", [""])[0], device_name(self.headers.get("User-Agent") or ""), self.client_address[0])
        if not token:
            self.close_connection = True
            return self.lan_page(403, "This pairing link doesn’t work any more",
                                 "Each code works once and only for 5 minutes. Make a new one on your computer: System → Phone and other devices.")
        secure = "; Secure" if getattr(self.server, "tls", False) else ""
        cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000{secure}"
        return self.lan_page(200, "Paired", "This device can now use your Tiiny Control Panel. Opening it…",
                             {"Set-Cookie": cookie}, refresh="/")

    def is_remote(self) -> bool:
        return bool(getattr(self.server, "lan", False))

    def local_auth(self, query):
        """A one-time sign-in link opened by the panel. Its page keeps the browser's credential in the browser's
        storage for this address, then shows the panel."""
        if not BROWSER.redeem(query.get("code", [""])[0]):
            self.close_connection = True
            return self.lan_page(403, "This sign-in link doesn’t work any more",
                                 f"Each link works once, for {CODE_TTL // 60} minutes. Start the panel again (from your app menu, "
                                 "or with python3 tiiny_ui.py) and it opens a fresh one.")
        esc = html_escape
        cred = BROWSER.credential(self.server.server_address[1])
        body = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<meta name="tiiny-credential" content="{esc(cred)}"><meta name="tiiny-storage-key" content="{esc(STORAGE_KEY)}">'
                '<title>Signing in…</title><body style="font:16px/1.5 system-ui,sans-serif;max-width:32em;margin:14vh auto;padding:0 20px">'
                '<h1 style="font-size:22px">Signing in…</h1><p id="msg">One moment.</p>'
                '<noscript><p>The Tiiny Control Panel needs JavaScript. Turn it on for this address, then start the panel again.</p></noscript>'
                '<script src="/static/js/signin.js"></script></body>')
        self.send_bytes(200, body.encode(), "text/html; charset=utf-8", {
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Referrer-Policy": "no-referrer"})

    def api_session(self):
        """Swap a signed-in browser's credential for this run's session token."""
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc.lower() not in self.allowed_hosts():
            self.close_connection = True
            return self.send_json(403, {"error": "Cross-origin request refused"})
        if not BROWSER.check_credential(self.headers.get("X-Tiiny-Credential") or "", self.server.server_address[1]):
            self.close_connection = True
            return self.send_json(401, {"error": "This browser isn't signed in to the panel. Open it from your app menu, "
                                                 "or run python3 tiiny_ui.py.", "session": "signed-out"})
        self.send_json(200, {"token": TOKEN})

    def serve_sw(self):
        try:
            data = (STATIC_DIR / "sw.js").read_bytes()
        except OSError:
            return self.send_text(404, "Not found")
        self.send_bytes(200, data, "text/javascript; charset=utf-8", {"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    def serve_static(self, rel):
        target = (STATIC_DIR / urllib.parse.unquote(rel)).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return self.send_text(404, "Not found")
        if not target.is_file():
            return self.send_text(404, "Not found")
        ctype = guess_mime(target.name)
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self.send_bytes(200, target.read_bytes(), ctype)

    # -- API: basics ----------------------------------------------------------------------
    def api_health(self, q):
        self.send_json(200, {
            "version": VERSION,
            "cli": {"found": bool(RT.bin), "path": RT.bin, "version": RT.cli_version, "note": RT.bin_note,
                    "install_cmd": INSTALL_CMD},
            "device": DeviceConn.public(DEVCONN.resolve()),
            "settings": SETTINGS.public(),
            "python": sys.version.split()[0],
            "remote": self.is_remote(),
            "commands": dict(commands.environment(), enabled=True) if ALLOW_COMMANDS and not self.is_remote() else {"enabled": False},
            "update": selfupdate.summary(),
        })

    def api_settings(self, q):
        body = self.read_json()
        locked = [k for k in STARTUP_ONLY_SETTINGS if k in body]
        if locked:
            raise BadRequest("For safety, the tiiny program and its config file can only be set when starting the panel "
                             "(python3 tiiny_ui.py --tiiny PATH --cli-config FILE).")
        if "search_engine" in body and body["search_engine"] not in websearch.ENGINES:
            raise BadRequest("Choose DuckDuckGo, Brave Search or SearXNG.")
        url = str(body.get("search_searxng_url") or "").strip()
        if url and not re.match(r"^https?://[^\s/]+", url):
            raise BadRequest("The SearXNG address should start with http:// or https://")
        if "decisions_off" in body:
            if not isinstance(body["decisions_off"], list):
                raise BadRequest("decisions_off is a list of decision features.")
            body["decisions_off"] = [f for f in body["decisions_off"] if f in decide.FEATURES]
        changes = {k: v for k, v in body.items() if k in Settings.FIELDS}
        SETTINGS.update(changes)
        self.api_health(q)

    def api_device_key(self, q):
        info = DEVCONN.resolve()
        self.send_json(200, {"key": info["key"], "address": info["address"], "port": info["port"]})

    def api_discover(self, q):
        info = DEVCONN.resolve()
        devices = discover_devices(extra_targets=[info["address"]], wait=clamp_int(q.get("wait", ["2"])[0], 1, 8, 2))
        self.send_json(200, {"devices": devices, "current": info["address"]})

    def api_decide(self, q):
        """A decision for one of the page's features (see tiinyui/decide.py). {"unavailable": why} means: fall back."""
        body = self.read_json()
        feature = str(body.get("feature") or "")
        if feature not in decide.FEATURES:
            raise BadRequest("Unknown decision feature.")
        title = str(body.get("title") or "").strip()[:80] or None
        try:
            with activity.labelled(title, activity.route(body.get("from")), self.is_remote()):
                out = decide.decide(DEVCONN, SETTINGS, feature, body.get("state"), body.get("questions"), body.get("images"),
                                    allow_load=body.get("load", True) is not False, timeout=clamp_int(body.get("timeout") or 60, 5, 300, 60))
        except ValueError as e:
            raise BadRequest(str(e))
        except decide.Unavailable as e:
            out = {"unavailable": str(e)}
        self.send_json(200, out)

    def api_firmware(self, q):
        """TiinyOS version and updates (see tiinyui/firmware.py). While the device restarts it just isn't reachable."""
        try:
            out = firmware.status(DEVCONN.resolve(), q.get("job", [""])[0])
        except DeviceError as e:
            out = {"service": "unknown", "reachable": False, "error": str(e)}
        out["updater"] = firmware.updater_info(JOBS)  # the download-then-install this panel is running
        self.send_json(200, out)

    def api_firmware_action(self, action, q):
        body = self.read_json()
        info = DEVCONN.resolve()
        try:
            if action == "check":
                out = firmware.check(info)
            elif action == "install":
                firmware.start_update(DEVCONN, JOBS, body.get("version"))
                out = firmware.status(info)
            elif action == "cancel":
                out = firmware.cancel(DEVCONN, JOBS)
            else:
                out = firmware.reboot(info)
        except DeviceError as e:
            raise BadRequest(str(e))
        out["updater"] = firmware.updater_info(JOBS)
        self.send_json(200, out)

    def api_overview(self, q):
        """Everything the dashboard needs, fetched from the device in parallel."""
        info = DEVCONN.resolve()
        jobs = {
            "device": ("discovery", "GET", "/device.json", None),
            "sys": ("core", "GET", "/api/v1/sys/status", None),
            "npu": ("core", "GET", "/api/v1/npu/status", None),
            "running": ("core", "GET", "/v1/models", None),
            "instances": ("core", "GET", "/api/v1/models/running", None),
            "account": ("auth", "POST", "/api/v1/connect", {}),
        }
        futures = {name: POOL.submit(device_json, info, *job[:3], body=job[3], timeout=8) for name, job in jobs.items()}
        out = {name: f.result() for name, f in futures.items()}
        out["conn"] = DeviceConn.public(info)
        out["now"] = self.right_now(info, out)
        out["borrowed"] = METRICS.borrowed_status()
        self.send_json(200, out)

    @staticmethod
    def right_now(info, overview):
        """What the device is doing (tiinyui/activity.py), from the overview's task list and this panel's jobs."""
        npu = (overview.get("npu") or {}).get("json")
        apps = {d["app"] for d in activity.device_tasks(npu)}
        names = activity.app_names(lambda: device_call(info, "core", "GET", "/api/v1/apps", timeout=8)[1], apps)
        return activity.now(npu, (overview.get("instances") or {}).get("json"), [j.info() for j in JOBS.list() if not j.finished], names)

    # -- API: device HTTP proxy ---------------------------------------------------------
    def _dev_request(self, body):
        info = DEVCONN.resolve()
        service = body.get("service") or "core"
        if service not in SERVICES:
            raise BadRequest(f"Unknown device service {service!r}")
        method = str(body.get("method") or "GET").upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise BadRequest("Unsupported method")
        path = build_path(body.get("path"), body.get("query"))
        headers = {}
        for k, v in (body.get("headers") or {}).items():
            name = FORWARD_HEADERS.get(str(k).lower())
            if name and isinstance(v, str) and "\n" not in v and "\r" not in v:
                headers[name] = v
        payload = None
        if body.get("form") is not None:
            payload, ctype, length = multipart_body(body["form"], RT)
            headers["Content-Type"] = ctype
            headers["Content-Length"] = str(length)
        elif "json" in body:
            payload = json.dumps(body["json"]).encode()
            headers["Content-Type"] = "application/json"
        timeout = clamp_int(body.get("timeout"), 1, 3600, 60)
        label = f"{method} {path}" + ("" if service == "core" else f"  (Host: {SERVICES[service][1] or service})")
        conn, resp = device_open(info, service, method, path, body=payload, headers=headers, timeout=timeout)
        return conn, resp, label

    def dev_activity(self, body):
        """Model work sent through the device proxy (images, speech, …) shows on the dashboard while it runs."""
        src = body.get("json")
        if not isinstance(src, dict):
            form = body.get("form")
            src = form.get("fields") if isinstance(form, dict) else None
        model = src.get("model") if isinstance(src, dict) else None
        method = str(body.get("method") or "GET").upper()
        path = str(body.get("path") or "") if method == "POST" and (body.get("service") or "core") == "core" else ""
        return activity.track(path, model, href=activity.route(body.get("from")), phone=self.is_remote())

    def api_dev(self, q):
        body = self.read_json()
        with self.dev_activity(body):
            self.dev_relay(body)

    def dev_relay(self, body):
        t0 = time.time()
        for src in (body.get("json"), body.get("query"), (body.get("form") or {}).get("fields") if isinstance(body.get("form"), dict) else None):
            if isinstance(src, dict):
                USAGE.touch(src.get("model"))
        m = re.match(r"^/api/v1/models/(.+)/start$", str(body.get("path") or ""))
        if m and str(body.get("method", "")).upper() == "POST":
            USAGE.touch(urllib.parse.unquote(m.group(1)))
        try:
            conn, resp, label = self._dev_request(body)
        except DeviceError as e:
            return self.send_json(200, {"ok": False, "status": None, "error": str(e), "duration": round(time.time() - t0, 3)})
        try:
            ok = 200 <= resp.status < 300
            ctype = resp.headers.get("Content-Type") or ""
            out = {"ok": ok, "status": resp.status, "content_type": ctype, "request": label}
            save = body.get("save")
            if save and ok:
                name = safe_name(str(save)) or "download"
                cd = resp.headers.get("Content-Disposition") or ""
                m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?", cd)
                if m and body.get("save_use_remote_name"):
                    name = safe_name(urllib.parse.unquote(m.group(1) or m.group(2))) or name
                path = RT.new_output_path(name)
                with open(path, "wb") as f:
                    total = 0
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_UPLOAD:
                            raise BadRequest("The device sent more data than expected.")
                        f.write(chunk)
                out["file"] = RT.register(path, os.path.basename(path), "output")
            else:
                raw = resp.read(MAX_DEVICE_RESPONSE)
                text = raw.decode("utf-8", "replace")
                parsed = None
                if text.strip()[:1] in ("{", "[") or "json" in ctype:
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        parsed = None
                out["json"] = parsed
                out["text"] = None if parsed is not None else text[:200_000]
                if not ok:
                    out["error"] = error_message(parsed, text, resp.status)
        except (OSError, http.client.HTTPException) as e:
            out = {"ok": False, "status": None, "error": f"The device connection dropped: {e}"}
        finally:
            conn.close()
        out["duration"] = round(time.time() - t0, 3)
        self.send_json(200, out)

    def api_dev_stream(self, q):
        """Like /api/dev but relays the response as it arrives (model import progress, ...)."""
        body = self.read_json()
        with self.dev_activity(body):
            self.dev_stream_relay(body)

    def dev_stream_relay(self, body):
        try:
            conn, resp, label = self._dev_request(body)
        except DeviceError as e:
            self.sse_start()
            self.sse("done", {"ok": False, "status": None, "error": str(e)})
            return
        try:
            self.sse_start()
            ctype = resp.headers.get("Content-Type") or ""
            self.sse("start", {"status": resp.status, "content_type": ctype, "request": label})
            if not 200 <= resp.status < 300:
                text = resp.read(100_000).decode("utf-8", "replace")
                try:
                    data = json.loads(text)
                except ValueError:
                    data = None
                self.sse("done", {"ok": False, "status": resp.status, "error": error_message(data, text, resp.status)})
                return
            event, data_lines = "message", []
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if "event-stream" in ctype:
                    if not line:
                        if data_lines:
                            self.sse("event", {"event": event, "data": "\n".join(data_lines)})
                        event, data_lines = "message", []
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                elif line.strip():
                    self.sse("line", {"text": line})
            if data_lines:
                self.sse("event", {"event": event, "data": "\n".join(data_lines)})
            self.sse("done", {"ok": True, "status": resp.status})
        finally:
            conn.close()

    def api_dev_icon(self, q):
        path = q.get("path", [""])[0]
        if not re.fullmatch(r"/api/v1/models/[\w./\-]+", path) or "icon" not in path or ".." in path:
            return self.send_text(404, "Not found")
        try:
            conn, resp = device_open(DEVCONN.resolve(), "core", "GET", path, headers={"Accept": "image/*"}, timeout=10)
        except DeviceError:
            return self.send_text(502, "Device unreachable")
        try:
            data = resp.read(2_000_000)
            ctype = resp.headers.get("Content-Type") or "image/png"
            if resp.status != 200 or not ctype.startswith("image/") or "svg" in ctype:
                return self.send_text(404, "Not found")
            self.send_bytes(200, data, ctype, {"Cache-Control": "private, max-age=86400"})
        finally:
            conn.close()

    # -- API: chat through the device's OpenAI-compatible endpoint ----------------------
    def api_chat(self, q):
        body = self.read_json()
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise BadRequest("messages must be a non-empty list")
        model = str(body.get("model") or "default")
        USAGE.touch(model)
        payload = {"model": model, "messages": messages, "stream": True}
        for k in ("temperature", "top_p", "max_tokens"):
            v = body.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                payload[k] = v
        if isinstance(body.get("tools"), list) and body["tools"]:
            payload["tools"] = body["tools"][:64]
            choice = body.get("tool_choice")
            if choice in ("auto", "none", "required") or isinstance(choice, dict):
                payload["tool_choice"] = choice
        extras = {}
        thinking = body.get("thinking")
        if isinstance(thinking, bool):
            extras["chat_template_kwargs"] = {"enable_thinking": thinking}
            depth = body.get("depth")
            if thinking and isinstance(depth, str) and re.fullmatch(r"[a-z]{2,12}", depth):
                extras["reasoning_effort"] = depth
        title = str(body.get("title") or "").strip()[:120] or None
        info = DEVCONN.resolve()
        self.sse_start()
        attempts = [dict(payload, **extras), payload] if extras else [payload]
        with activity.track("/v1/chat/completions", model, title=title, href=activity.route(body.get("from")),
                            phone=self.is_remote()):
            for i, body_obj in enumerate(attempts):
                t_sent = time.time()
                try:
                    conn, resp = device_open(info, "core", "POST", "/v1/chat/completions", body=json.dumps(body_obj).encode(),
                                             headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
                                             timeout=600)
                except DeviceError as e:
                    self.sse("error", {"message": str(e)})
                    return
                if resp.status >= 400:
                    text = resp.read(20_000).decode("utf-8", "replace")
                    conn.close()
                    try:
                        data = json.loads(text)
                    except ValueError:
                        data = None
                    if extras and i == 0 and resp.status in (400, 422):
                        self.sse("info", {"text": "The device didn't accept the thinking options, so this reply uses the "
                                                  "model's default thinking mode."})
                        continue
                    self.sse("error", {"message": f"The device answered HTTP {resp.status}: {error_message(data, text, resp.status)}",
                                       "status": resp.status})
                    return
                try:
                    self.relay_completion(resp, model, t_sent)
                finally:
                    conn.close()
                return

    def relay_completion(self, resp, model, t_sent=None):
        usage, finish, served = None, None, model
        t_first = t_last = None
        pieces = 0
        if "event-stream" not in (resp.headers.get("Content-Type") or ""):
            raw = resp.read()
            try:
                data = json.loads(raw)
            except ValueError:
                self.sse("error", {"message": "Unexpected reply from the device: " + raw[:300].decode("utf-8", "replace")})
                return
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            self.sse("delta", {"content": text_of(msg.get("content")),
                               "reasoning": text_of(msg.get("reasoning_content") or msg.get("reasoning"))})
            if msg.get("tool_calls"):
                self.sse("tool_delta", {"tool_calls": [dict(tc, index=i) for i, tc in enumerate(msg["tool_calls"])]})
            self.sse("done", {"usage": data.get("usage"), "finish_reason": choice.get("finish_reason"),
                              "model": data.get("model") or model})
            return
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("error"):
                err = obj["error"]
                self.sse("error", {"message": err.get("message", str(err)) if isinstance(err, dict) else str(err)})
                return
            served = obj.get("model") or served
            if obj.get("usage"):
                usage = obj["usage"]
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or choice.get("message") or {}
                content = text_of(delta.get("content"))
                reasoning = text_of(delta.get("reasoning_content") or delta.get("reasoning"))
                if content or reasoning or delta.get("tool_calls"):
                    t_last = time.time()
                    t_first = t_first or t_last
                    pieces += 1
                if content or reasoning:
                    self.sse("delta", {"content": content, "reasoning": reasoning})
                if delta.get("tool_calls"):
                    self.sse("tool_delta", {"tool_calls": delta["tool_calls"]})
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        self.sse("done", {"usage": usage, "finish_reason": finish, "model": served})
        if t_first and t_sent:  # feeds the speed leaderboard
            tokens = (usage or {}).get("completion_tokens") or pieces
            gen = t_last - t_first
            METRICS.record_speed(served, t_first - t_sent, (tokens - 1) / gen if gen > 0.2 and tokens > 1 else None, tokens)

    # -- API: data store ------------------------------------------------------------------
    def api_store_list(self, coll, q):
        fields = [f for f in (q.get("fields", [""])[0]).split(",") if f]
        where = {}
        if "project_id" in q:
            where["project_id"] = q["project_id"][0] or None
        self.send_json(200, {"items": STORE.list(coll, fields or None, where or None)})

    def api_store_create(self, coll, q):
        body = self.read_json()
        rid = body.get("id") if valid_id(body.get("id")) else None
        self.send_json(200, STORE.put(coll, body, rid))

    def api_store_get(self, coll, rid, q):
        rec = STORE.get(coll, rid)
        if rec is None:
            return self.send_json(404, {"error": "Not found"})
        self.send_json(200, rec)

    def api_store_put(self, coll, rid, q):
        self.send_json(200, STORE.put(coll, self.read_json(), rid))

    def api_store_delete(self, coll, rid, q):
        self.read_json()
        if coll == "chats":  # files pinned to the chat live in a hidden project
            for p in STORE.list("projects", ["id", "chat_id"]):
                if p.get("chat_id") == rid:
                    STORE.delete("projects", p["id"])
        self.send_json(200, {"deleted": STORE.delete(coll, rid)})

    def api_store_import(self, q):
        body = self.read_json()
        added = 0
        for rec in body.get("items", [])[:2000]:
            if isinstance(rec, dict) and valid_id(rec.get("id")) and STORE.get("chats", rec["id"]) is None:
                STORE.put("chats", rec, rec["id"])
                added += 1
        self.send_json(200, {"imported": added})

    # -- API: background jobs ------------------------------------------------------------
    def _job(self, jid):
        job = JOBS.get(jid)
        if not job:
            raise BadRequest("That job no longer exists.")
        return job

    def api_jobs(self, q):
        kind = q.get("kind", [""])[0] or None
        meta = {k[5:]: v[0] for k, v in q.items() if k.startswith("meta.")}
        self.send_json(200, {"jobs": [j.info() for j in JOBS.list(kind, **meta)]})

    def api_job(self, jid, q):
        self.send_json(200, self._job(jid).info(with_log=True))

    def api_job_stream(self, jid, q):
        job = self._job(jid)
        self.sse_start()
        version = -1
        while True:
            if job.version != version:
                version = job.version
                self.sse("job", job.info(with_log=bool(q.get("log"))))
                if job.finished:
                    return
            else:
                self.wfile.write(b": ping\n\n")
            JobManager.wait(job, version, 15)

    def api_job_cancel(self, jid, q):
        self.read_json()
        self.send_json(200, {"cancelled": JOBS.cancel(jid)})

    # -- API: files on this computer, web pages --------------------------------------------
    def api_fs_list(self, q):
        raw = q.get("path", [""])[0] or str(Path.home())
        path = Path(raw).expanduser()
        try:
            path = path.resolve()
            entries = []
            show_hidden = bool(q.get("hidden"))
            with os.scandir(path) as it:
                for e in it:
                    if not show_hidden and e.name.startswith("."):
                        continue
                    try:
                        is_dir = e.is_dir(follow_symlinks=True)
                        st = e.stat(follow_symlinks=True)
                    except OSError:
                        continue
                    entries.append({"name": e.name, "dir": is_dir, "size": None if is_dir else st.st_size, "mtime": st.st_mtime})
                    if len(entries) >= 3000:
                        break
        except (FileNotFoundError, NotADirectoryError):
            raise BadRequest(f"{raw} isn't a folder.")
        except PermissionError:
            raise BadRequest(f"You don't have permission to open {raw}.")
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        self.send_json(200, {"path": str(path), "parent": str(path.parent) if path.parent != path else None,
                             "home": str(Path.home()), "entries": entries})

    def api_web_search(self, q):
        body = self.read_json()
        self.send_json(200, websearch.search(body.get("query", ""), body.get("count", 6), str(body.get("recency") or ""),
                                             engine=SETTINGS.get("search_engine"), brave_key=SETTINGS.get("search_brave_key"),
                                             searxng_url=SETTINGS.get("search_searxng_url")))

    # -- API: agent mode on this computer (only with --allow-commands; never for phones) --------------
    def api_command_run(self, q):
        body = self.read_json()
        if not ALLOW_COMMANDS:
            raise BadRequest(COMMANDS_OFF)
        spec = commands.prepare(body.get("command"), body.get("cwd"), body.get("timeout"))
        job = JOBS.start("command", f"Command: {spec['command'][:80]}", lambda job: commands.run(job, spec),
                         meta={"source": "agent", "cwd": spec["cwd"]})
        self.send_json(200, {"job": job.info()})

    def api_file_read(self, q):
        body = self.read_json()
        if not ALLOW_COMMANDS:
            raise BadRequest(COMMANDS_OFF)
        self.send_json(200, commands.read_file(body.get("path"), body.get("start_line"), body.get("max_lines")))

    def api_file_write(self, q):
        body = self.read_json()
        if not ALLOW_COMMANDS:
            raise BadRequest(COMMANDS_OFF)
        try:
            out = commands.write_file(body.get("path"), body.get("content"), bool(body.get("overwrite")), bool(body.get("append")))
        except OSError as e:
            raise BadRequest(f"Couldn't write the file: {e.strerror or e}") from None
        self.send_json(200, out)

    def api_file_find(self, q):
        body = self.read_json()
        if not ALLOW_COMMANDS:
            raise BadRequest(COMMANDS_OFF)
        self.send_json(200, commands.find_files(body.get("folder"), body.get("name"), body.get("text"), body.get("max_results"),
                                                bool(body.get("include_hidden"))))

    def api_file_open(self, q):
        body = self.read_json()
        if not ALLOW_COMMANDS:
            raise BadRequest(COMMANDS_OFF)
        self.send_json(200, commands.open_target(body.get("target")))

    # -- API: agent mode's history search (saved chats, meetings, the Gallery) ---------------------
    def api_history_search(self, q):
        body = self.read_json()
        self.send_json(200, history.search(STORE, str(body.get("query") or ""), str(body.get("where") or "all"),
                                           str(body.get("exclude_chat") or ""), body.get("limit", 8)))

    def api_history_read(self, q):
        body = self.read_json()
        self.send_json(200, history.read(STORE, str(body.get("kind") or ""), str(body.get("id") or "")))

    def api_web_fetch(self, q):
        body = self.read_json()
        page = webfetch.fetch(str(body.get("url", "")), allow_private=not self.is_remote())
        page["text"] = page["text"][:200_000]
        self.send_json(200, page)

    def api_web_to_vault(self, q):
        body = self.read_json()
        page = body.get("page") if isinstance(body.get("page"), dict) and body["page"].get("text") else webfetch.fetch(str(body.get("url", "")), allow_private=not self.is_remote())
        name = (safe_name(re.sub(r"[\\/:*?\"<>|]+", " ", page.get("title") or "page")).strip() or "page")[:100] + ".md"
        content = webfetch.page_markdown(page, time.strftime("%Y-%m-%d %H:%M")).encode("utf-8")
        out = device_vault_upload(DEVCONN.resolve(), RT.work / "uploads", name, content, finalize=bool(body.get("index", True)))
        self.send_json(200, dict(out, title=page.get("title"), url=page.get("final_url") or page.get("url")))

    # -- API: projects --------------------------------------------------------------------
    def api_projects(self, q):
        self.send_json(200, {"projects": PROJECTS.list()})

    def api_project_create(self, q):
        self.send_json(200, PROJECTS.create(self.read_json()))

    def api_project_get(self, pid, q):
        self.send_json(200, PROJECTS.get(pid))

    def api_project_update(self, pid, q):
        self.send_json(200, PROJECTS.update(pid, self.read_json()))

    def api_project_delete(self, pid, q):
        self.read_json()
        chats_mode = q.get("chats", ["keep"])[0]
        for c in STORE.list("chats", ["id", "project_id"]):
            if c.get("project_id") == pid:
                if chats_mode == "delete":
                    STORE.delete("chats", c["id"])
                else:
                    STORE.update("chats", c["id"], lambda r: r.update(project_id=None))
        PROJECTS.delete(pid)
        self.send_json(200, {"deleted": True})

    def api_project_add_doc(self, pid, q):
        spec = self.read_json()
        if self.is_remote() and spec.get("kind") == "folder":
            raise BadRequest("Folders can only be linked on the computer running the panel.")
        self.send_json(200, PROJECTS.add_doc(pid, spec, allow_private=not self.is_remote()))

    def api_project_doc(self, pid, did, q):
        self.send_json(200, PROJECTS.doc_text(pid, did))

    def api_project_remove_doc(self, pid, did, q):
        self.read_json()
        PROJECTS.remove_doc(pid, did)
        self.send_json(200, {"removed": True})

    def api_project_index(self, pid, q):
        self.read_json()
        self.send_json(200, PROJECTS.start_index(pid).info())

    def api_project_context(self, pid, q):
        body = self.read_json()
        self.send_json(200, PROJECTS.context(pid, str(body.get("question", "")), body.get("size"),
                                             clamp_int(body.get("start"), 1, 1000, 1)))

    def api_project_search(self, pid, q):
        body = self.read_json()
        hits = PROJECTS.search(pid, str(body.get("query", "")), clamp_int(body.get("k"), 1, 30, 8))
        self.send_json(200, {"results": hits})

    def api_project_read(self, pid, q):
        body = self.read_json()
        doc, path = PROJECTS.find_doc(pid, str(body.get("name", "")))
        if not doc:
            raise BadRequest(f"No file called {body.get('name')!r} in this project.")
        if path is not None:
            from tiinyui.files import read_text
            text = read_text(path, 120_000)
            return self.send_json(200, {"name": str(path.relative_to(Path(doc["path"]).resolve())), "text": text[:120_000],
                                        "truncated": len(text) >= 120_000})
        out = PROJECTS.doc_text(pid, doc["id"], 120_000)
        self.send_json(200, {"name": doc.get("name"), "text": out["text"], "truncated": out.get("truncated", False)})

    def api_project_memory(self, pid, q):
        body = self.read_json()
        job = PROJECTS.start_memory_update(pid, str(body.get("model") or "default"), str(body.get("user", "")),
                                           str(body.get("assistant", "")), str(body.get("chat_id", "")))
        self.send_json(200, {"job": job.info() if job else None})

    # -- API: gallery and studio ---------------------------------------------------------------
    def api_gallery(self, q):
        kind = q.get("kind", [""])[0] or None
        self.send_json(200, {"items": GALLERY.list(kind)})

    def api_gallery_add(self, q):
        rec = GALLERY.add(self.read_json())
        if rec.get("kind") == "image" and "gallery_tags" not in (SETTINGS.get("decisions_off") or []):
            GALLERY.tag_later(DEVCONN, rec["id"])  # a decision model tags it in the background, if one can
        self.send_json(200, rec)

    def api_gallery_tag(self, q):
        """Tag the pictures that have no tags yet ({"again": true}: all of them) with a decision model."""
        body = self.read_json()
        if "gallery_tags" in (SETTINGS.get("decisions_off") or []):
            raise BadRequest("Tagging pictures is switched off under System → Decision models.")
        running = JOBS.running("gallery-tags")
        if running:
            return self.send_json(200, running.info())
        job = JOBS.start("gallery-tags", "Tagging pictures", lambda job: GALLERY.tag_all(job, DEVCONN, bool(body.get("again"))))
        self.send_json(200, job.info())

    def api_gallery_get(self, gid, q):
        self.send_json(200, GALLERY.get(gid))

    def api_gallery_update(self, gid, q):
        self.send_json(200, GALLERY.update(gid, self.read_json()))

    def api_gallery_delete(self, gid, q):
        self.read_json()
        GALLERY.delete(gid)
        self.send_json(200, {"deleted": True})

    def api_gallery_delete_many(self, q):  # the Gallery's selection: {"ids": [...]}
        self.send_json(200, GALLERY.delete_many(self.read_json().get("ids")))

    def api_gallery_favorite_many(self, q):  # {"ids": [...], "favorite": true | false}
        body = self.read_json()
        self.send_json(200, GALLERY.favorite_many(body.get("ids"), body.get("favorite", True) is not False))

    def api_gallery_file(self, gid, name, q):
        path = GALLERY.file_path(gid, urllib.parse.unquote(name))
        self.send_file(str(path), path.name, q)

    def api_studio_start(self, kind, q):
        spec = self.read_json()
        job = {"music": STUDIO.start_music, "3d": STUDIO.start_3d, "video": STUDIO.start_video}[kind](spec)
        self.send_json(200, job.info())

    def api_job_control(self, jid, q):
        self.send_json(200, STUDIO.control(jid, str(self.read_json().get("action", ""))))

    # -- API: phones and other devices --------------------------------------------------------
    def lan_info(self):
        srv = LAN["server"]
        scheme = "https" if TLS else "http"
        return {"enabled": bool(SETTINGS.get("lan_enabled")), "running": srv is not None, "ip": LAN["ip"], "port": LAN["port"],
                "url": f"{scheme}://{LAN['ip']}:{LAN['port']}/" if srv else "", "tls": bool(TLS), "error": LAN["error"],
                "devices": PAIRING.list()}

    def api_lan(self, q):
        self.send_json(200, self.lan_info())

    def api_lan_set(self, q):
        on = bool(self.read_json().get("enabled"))
        SETTINGS.update({"lan_enabled": on})
        if on:
            lan_start()
        else:
            lan_stop()
        self.send_json(200, self.lan_info())

    def api_lan_pair(self, q):
        self.read_json()
        if not LAN["server"]:
            raise BadRequest("Turn on access from other devices first.")
        code, ttl = PAIRING.new_code()
        scheme = "https" if TLS else "http"
        self.send_json(200, {"url": f"{scheme}://{LAN['ip']}:{LAN['port']}/pair?code={code}", "expires_in": ttl})

    def api_lan_revoke(self, sid, q):
        self.read_json()
        self.send_json(200, {"revoked": PAIRING.revoke(sid)})

    # -- API: app menu entry, stopping the panel -------------------------------------------------
    def api_launcher(self, q):
        self.send_json(200, launcher.status())

    def api_launcher_set(self, q):
        body = self.read_json()
        try:
            out = launcher.install(Path(__file__), STATIC_DIR, START_ARGS) if body.get("installed") else launcher.remove()
        except OSError as e:
            raise BadRequest(f"Couldn't change the app menu: {e}")
        self.send_json(200, out)

    def api_shutdown(self, q):
        self.read_json()
        self.send_json(200, {"stopping": True})
        if HTTPD is not None:
            threading.Timer(0.3, HTTPD.shutdown).start()

    # -- API: updating the panel (tiinyui/selfupdate.py) ----------------------------------------------
    @staticmethod
    def busy_jobs():
        """The panel's background work a restart would stop."""
        return [j.title for j in JOBS.list() if not j.finished and j.kind != "update"]

    def api_update(self, q):
        st = selfupdate.status()
        job = JOBS.running("update") or next(iter(JOBS.list("update")), None)
        st["job"] = job.info(with_log=True) if job else None
        st["busy"] = self.busy_jobs()
        self.send_json(200, st)

    def api_update_check(self, q):
        self.read_json()
        selfupdate.check(force=True)
        self.api_update(q)

    def api_update_install(self, q):
        self.read_json()
        if JOBS.running("update"):
            raise BadRequest("The panel is already updating.")

        def work(job):
            try:
                return selfupdate.install(job)
            except selfupdate.UpdateError as e:
                raise BadRequest(str(e))
        self.send_json(200, JOBS.start("update", "Updating the panel", work).info())

    def api_update_restart(self, q):
        body = self.read_json()
        busy = self.busy_jobs()
        if busy and not body.get("force"):
            return self.send_json(200, {"restarting": False, "busy": busy})
        if HTTPD is None:
            raise BadRequest("The panel can't restart itself right now.")
        self.send_json(200, {"restarting": True})
        RESTART["wanted"] = True
        threading.Timer(0.4, HTTPD.shutdown).start()

    # -- API: a backup of the panel's own data (tiinyui/backup.py) ------------------------------------
    def api_backup(self, q):
        self.send_json(200, dict(backup.summary(STORE), job=(lambda j: j.info() if j else None)(JOBS.running("backup"))))

    def api_backup_export(self, q):
        body = self.read_json()
        if JOBS.running("backup"):
            raise BadRequest("A backup is already being made or restored. Wait for it to finish.")
        metrics = bool(body.get("metrics"))

        def work(job):
            name = f"tiiny-panel-backup-{time.strftime('%Y-%m-%d')}.zip"
            path = RT.new_output_path(name)
            job.update(message="Packing your data…")
            manifest = backup.export(STORE, SETTINGS, path, metrics=metrics, job=job)
            return {"file": RT.register(path, name, "output"), "manifest": manifest}
        self.send_json(200, JOBS.start("backup", "Backing up the panel's data", work, {"action": "export"}).info())

    def _backup_upload(self, body) -> str:
        rec = RT.get_file(str(body.get("file") or ""))
        if not rec or not os.path.isfile(rec["path"]):
            raise BadRequest("That uploaded file has expired. Please add it again.")
        return rec["path"]

    def api_backup_inspect(self, q):
        self.send_json(200, backup.inspect(STORE, self._backup_upload(self.read_json())))

    def api_backup_restore(self, q):
        body = self.read_json()
        path = self._backup_upload(body)
        mode = str(body.get("mode") or "merge")
        busy = [j.title for j in JOBS.list() if not j.finished]
        if busy:
            raise BadRequest(f"Wait until this has finished, then restore: {', '.join(busy[:3])}.")

        def work(job):
            job.update(message="Restoring…")
            return backup.restore(STORE, SETTINGS, path, mode=mode, with_settings=bool(body.get("settings")), job=job)
        self.send_json(200, JOBS.start("backup", "Restoring the panel's data", work, {"action": "restore"}).info())

    # -- API: performance history -------------------------------------------------------------
    def api_metrics(self, q):
        self.send_json(200, METRICS.query(q.get("range", ["1h"])[0]))

    def api_metrics_speed(self, q):
        self.send_json(200, {"models": METRICS.speed_board()})

    def api_metrics_idle(self, q):
        self.send_json(200, {"enabled": bool(SETTINGS.get("idle_unload")), "minutes": SETTINGS.get("idle_minutes"),
                             "keep_loaded": SETTINGS.get("keep_loaded") or [], "models": METRICS.idle_status(),
                             "events": [e for e in list(METRICS.events)[-50:] if e["kind"] == "auto-unload"],
                             "borrowed": METRICS.borrowed_status()})

    def api_models_borrowed(self, q):
        """🎤 and 🔊 borrow a model they have to load, and give it back when they're done; the panel then unloads it
        once nothing has used it for `after` seconds (common.Borrowed). "keep": something else relies on it now."""
        b = self.read_json()
        model, state, session = b.get("model"), b.get("state"), b.get("session")
        if not isinstance(model, str) or not model.strip() or len(model) > 300:
            raise BadRequest("Which model?")
        if state in ("using", "done") and not (isinstance(session, str) and re.fullmatch(r"[\w-]{1,64}", session)):
            raise BadRequest("Missing or invalid session")
        if state == "using":
            ok = BORROWED.borrow(model, session, loaded=bool(b.get("loaded")), why="voice", after=clamp_int(b.get("after"), 0, 3600, 120))
        elif state == "done":
            ok = BORROWED.give_back(model, session, clamp_int(b.get("after"), 0, 3600, 120))
        elif state == "keep":
            ok = BORROWED.keep(model)
        else:
            raise BadRequest("state must be using, done or keep")
        self.send_json(200, {"ok": ok, "borrowed": METRICS.borrowed_status()})

    # -- API: meetings ---------------------------------------------------------------------------
    def api_meetings(self, q):
        self.send_json(200, {"meetings": MEETINGS.list()})

    def api_meeting_create(self, q):
        self.send_json(200, MEETINGS.create(self.read_json()))

    def api_meeting_get(self, mid, q):
        self.send_json(200, MEETINGS.get(mid))

    def api_meeting_update(self, mid, q):
        self.send_json(200, MEETINGS.update(mid, self.read_json()))

    def api_meeting_delete(self, mid, q):
        self.read_json()
        MEETINGS.delete(mid)
        self.send_json(200, {"deleted": True})

    def api_meeting_segments(self, mid, q):
        segs = self.read_json().get("segments")
        if not isinstance(segs, list):
            raise BadRequest("segments must be a list")
        self.send_json(200, MEETINGS.add_segments(mid, segs))

    def api_meeting_summarize(self, mid, q):
        self.send_json(200, MEETINGS.start_summary(mid, str(self.read_json().get("model") or "")).info())

    def api_meeting_highlights(self, mid, q):
        self.read_json()
        self.send_json(200, MEETINGS.start_highlights(mid).info())

    def api_meeting_to_vault(self, mid, q):
        self.read_json()
        self.send_json(200, MEETINGS.to_vault(mid))

    def api_meeting_to_project(self, mid, q):
        self.send_json(200, MEETINGS.to_project(mid, str(self.read_json().get("project_id", ""))))

    def api_meeting_export(self, mid, q):
        name, md = MEETINGS.markdown(mid)
        self.send_bytes(200, md.encode("utf-8"), "text/markdown; charset=utf-8",
                        {"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})

    # -- API: code review ---------------------------------------------------------------------
    def api_reviews(self, q):
        self.send_json(200, {"reviews": REVIEWS.list()})

    def api_review_create(self, q):
        spec = self.read_json()
        if self.is_remote() and spec.get("kind") != "zip":
            raise BadRequest("From a phone, upload a ZIP file to review. Folders can only be reviewed on the computer itself.")
        self.send_json(200, REVIEWS.create(spec))

    def api_review_get(self, rid, q):
        self.send_json(200, REVIEWS.get(rid))

    def api_review_delete(self, rid, q):
        self.read_json()
        REVIEWS.delete(rid)
        self.send_json(200, {"deleted": True})

    def api_review_start(self, rid, q):
        self.send_json(200, REVIEWS.start(rid, self.read_json()).info())

    def api_review_select(self, rid, q):
        REVIEWS.select(rid, [str(p) for p in self.read_json().get("files", [])])
        self.send_json(200, {"ok": True})

    def api_review_mark(self, rid, fid, q):
        REVIEWS.mark(rid, fid, str(self.read_json().get("state", "")))
        self.send_json(200, {"ok": True})

    def api_review_code(self, rid, q):
        start = clamp_int(q.get("start", ["1"])[0], 1, 10_000_000, 1)
        self.send_json(200, REVIEWS.code(rid, q.get("path", [""])[0], start, clamp_int(q.get("end", [str(start)])[0], 1, 10_000_000, start)))

    def api_review_export(self, rid, q):
        name, ctype, data = REVIEWS.export(rid, q.get("format", ["md"])[0])
        self.send_bytes(200, data, f"{ctype}; charset=utf-8",
                        {"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})

    # -- API: one-shot CLI commands -------------------------------------------------------
    def api_cli(self, q):
        body = self.read_json()
        res = RT.run(body.get("args"), json_out=bool(body.get("json")), yes=bool(body.get("yes")),
                     timeout=clamp_int(body.get("timeout"), 1, 7200, 120))
        self.send_json(200, res)

    # -- API: pseudo-terminal sessions ------------------------------------------------
    def api_pty_list(self, q):
        self.send_json(200, {"sessions": [s.info() for s in PTYS.all()]})

    def api_pty_start(self, q):
        body = self.read_json()
        args = body.get("args")
        line = body.get("line")
        if isinstance(line, str):
            try:
                args = shlex.split(line)
            except ValueError as e:
                raise BadRequest(f"Couldn't parse that command: {e}")
            if args and os.path.basename(args[0]) in ("tiiny", os.path.basename(RT.bin or "tiiny")):
                args = args[1:]
        expanded, _ = RT.expand(args)
        argv = RT.argv(expanded)
        s = PTYS.start(argv, RT.env(tty=True), RT.cwd,
                       clamp_int(body.get("cols"), 20, 400, 100), clamp_int(body.get("rows"), 5, 200, 30))
        self.send_json(200, s.info())

    def _session(self, sid):
        s = PTYS.get(sid)
        if not s:
            raise BadRequest("That terminal session no longer exists.")
        return s

    def api_pty_stream(self, sid, q):
        s = self._session(sid)
        offset = clamp_int(q.get("offset", ["0"])[0], 0, 1 << 60, 0)
        self.sse_start()
        self.sse("hello", s.info())
        while True:
            end, chunk, ended = s.read(offset, 15)
            if chunk:
                offset = end
                self.sse("out", {"o": end, "d": base64.b64encode(chunk).decode()})
            elif ended:
                self.sse("exit", {"code": s.code})
                return
            else:
                self.wfile.write(b": ping\n\n")

    def api_pty_input(self, sid, q):
        body = self.read_json()
        data = body.get("data")
        if not isinstance(data, str):
            raise BadRequest("data must be a string")
        self._session(sid).write(data.encode("utf-8"))
        self.send_json(200, {"ok": True})

    def api_pty_resize(self, sid, q):
        body = self.read_json()
        self._session(sid).resize(clamp_int(body.get("cols"), 20, 400, 100), clamp_int(body.get("rows"), 5, 200, 30))
        self.send_json(200, {"ok": True})

    def api_pty_kill(self, sid, q):
        self.read_json()
        s = self._session(sid)
        s.kill()
        self.send_json(200, s.info())

    # -- API: files ---------------------------------------------------------------------
    def api_upload(self, q):
        name = safe_name(q.get("name", [""])[0]) or "upload.bin"
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            raise BadRequest("Content-Length is required")
        if length > MAX_UPLOAD:
            self.close_connection = True
            return self.send_json(413, {"error": "That file is too large."})
        d = tempfile.mkdtemp(dir=RT.work / "uploads")
        path = os.path.join(d, name)
        remaining = length
        with open(path, "wb") as f:
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        if remaining:
            shutil.rmtree(d, ignore_errors=True)
            self.close_connection = True
            raise BadRequest("The upload was interrupted.")
        self.send_json(200, RT.register(path, name, "upload"))

    def api_file(self, fid, q):
        rec = RT.get_file(fid)
        if not rec or not os.path.isfile(rec["path"]):
            return self.send_json(404, {"error": "That file has expired."})
        self.send_file(rec["path"], rec["name"], q)

    def send_file(self, path: str, name: str, q):
        """Serve a local file with Range support (audio and video seeking)."""
        size = os.path.getsize(path)
        mime = guess_mime(name)
        start, end, status = 0, size - 1, 200
        rng = (self.headers.get("Range") or "").strip()
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
        if m and (m.group(1) or m.group(2)) and size:
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        download = bool(q.get("download")) or mime in RISKY_MIME
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        disposition = "attachment" if download else "inline"
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{urllib.parse.quote(name)}")
        self.send_header("Content-Security-Policy", FILE_CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def text_of(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(p.get("text", "") for p in value if isinstance(p, dict))
    return ""


def lan_start():
    """The second listener, on this computer's local-network address, for paired devices only."""
    with LAN_LOCK:
        if LAN["server"]:
            return
        ip = lan_ip_towards(DEVCONN.resolve().get("address") or "")
        if not ip:
            LAN["error"] = "Couldn't find this computer's address on your network."
            raise BadRequest(LAN["error"])
        port = int(SETTINGS.get("lan_port") or 0) or (HTTPD.server_address[1] if HTTPD else 8765)
        try:
            srv = Server((ip, port), Handler)
        except OSError as e:
            LAN["error"] = f"Couldn't listen on {ip}:{port}: {e}"
            raise BadRequest(LAN["error"])
        srv.lan, srv.check_host, srv.lan_host, srv.tls = True, True, f"{ip}:{port}", bool(TLS)
        if TLS:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(TLS["cert"], TLS["key"])
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True, name="lan").start()
        LAN.update(server=srv, ip=ip, port=port, error="")


def lan_stop():
    with LAN_LOCK:
        srv = LAN["server"]
        if not srv:
            return
        LAN.update(server=None, error="")
    srv.shutdown()
    srv.server_close()


def already_running(port: int, auth: BrowserAuth) -> bool:
    """Is this account's panel already serving on this port? (Then a second start just opens it, signed in.)
    It must prove it knows the browser key, so a sign-in link never goes to some other program on the port."""
    challenge = secrets.token_hex(16)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__tiiny__?challenge={challenge}", timeout=1.5) as r:
            info = json.loads(r.read(4096))
    except (OSError, ValueError):
        return False
    return isinstance(info, dict) and info.get("app") == "tiiny-ui" and \
        hmac.compare_digest(str(info.get("proof", "")), auth.proof(challenge))


def open_signed_in(url: str):
    link = BROWSER.link(url)
    if not webbrowser.open(link):
        print(f"Couldn't open a browser. Sign in with this link (it works once, for {CODE_TTL // 60} minutes):\n  {link}")
        sys.stdout.flush()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    check_host = True

    def handle_error(self, request, client_address):
        # A browser closing a tab mid-request is normal; only print real problems.
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main():
    global SETTINGS, RT, DEVCONN, STORE, PROJECTS, REVIEWS, GALLERY, STUDIO, MEETINGS, METRICS, PAIRING, BROWSER, HTTPD, VERBOSE, ALLOW_COMMANDS
    ap = argparse.ArgumentParser(description="Local web interface for your Tiiny device.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("TIINY_UI_PORT", "8765")))
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default 127.0.0.1; anything else exposes full control of your device)")
    ap.add_argument("--tiiny", default="", help="path to the tiiny binary (default: search the usual places)")
    ap.add_argument("--cli-config", default="", help="the tiiny CLI's config file (default ~/.local/tiiny/config.json)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    ap.add_argument("--allow-commands", action="store_true",
                    help="let agent mode run commands and read and write files on this computer (it asks before each one)")
    ap.add_argument("--verbose", action="store_true", help="log every HTTP request")
    ap.add_argument("--tls-cert", default="", help="certificate (PEM) for HTTPS on the local-network listener used by phones")
    ap.add_argument("--tls-key", default="", help="private key (PEM) for --tls-cert")
    args = ap.parse_args()
    VERBOSE = args.verbose

    if not (STATIC_DIR / "index.html").is_file():
        sys.exit(f"Missing {STATIC_DIR / 'index.html'}; keep tiiny_ui.py next to its static/ and tiinyui/ folders.")
    BROWSER = BrowserAuth(CONFIG_DIR / "browser-key")
    if args.host in ("127.0.0.1", "localhost", "::1") and already_running(args.port, BROWSER):
        url = f"http://127.0.0.1:{args.port}/"
        print(f"The Tiiny Control Panel is already running at {url}")
        if args.no_browser:
            print(f"Sign your browser in with this link (it works once, for {CODE_TTL // 60} minutes):\n  {BROWSER.link(url)}")
        else:
            open_signed_in(url)
        return
    if args.tiiny:
        START_ARGS.extend(["--tiiny", os.path.abspath(os.path.expanduser(args.tiiny))])
    if args.cli_config:
        START_ARGS.extend(["--cli-config", os.path.abspath(os.path.expanduser(args.cli_config))])
    if args.port != 8765:
        START_ARGS.extend(["--port", str(args.port)])
    if args.tls_cert and args.tls_key:
        START_ARGS.extend(["--tls-cert", os.path.abspath(args.tls_cert), "--tls-key", os.path.abspath(args.tls_key)])
    if args.allow_commands:
        START_ARGS.append("--allow-commands")
    ALLOW_COMMANDS = bool(args.allow_commands)
    SETTINGS = Settings(SETTINGS_PATH)
    if args.cli_config:
        SETTINGS.override("cli_config", os.path.expanduser(args.cli_config))
    RT = Runtime(SETTINGS, args.tiiny)
    DEVCONN = DeviceConn(SETTINGS)
    STORE = Store(data_dir())
    decide.use_settings(SETTINGS)
    PROJECTS = Projects(STORE, DEVCONN, JOBS, RT)
    REVIEWS = Reviews(STORE, DEVCONN, JOBS, RT, PROJECTS)
    GALLERY = Gallery(STORE, RT)
    STUDIO = Studio(STORE, DEVCONN, JOBS, RT, GALLERY)
    MEETINGS = Meetings(STORE, DEVCONN, JOBS, RT, PROJECTS)
    METRICS = Metrics(DEVCONN, SETTINGS, JOBS, STORE.root / "metrics")
    PAIRING = Pairing(CONFIG_DIR / "devices.json")
    if bool(args.tls_cert) != bool(args.tls_key):
        sys.exit("--tls-cert and --tls-key go together.")
    if args.tls_cert:
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(args.tls_cert, args.tls_key)
        except (OSError, ssl.SSLError) as e:
            sys.exit(f"Couldn't use that certificate: {e}")
        TLS.update(cert=args.tls_cert, key=args.tls_key)

    httpd, last_error = None, None
    for port in range(args.port, args.port + 10):
        try:
            httpd = Server((args.host, port), Handler)
            break
        except OSError as e:
            last_error = e
    if httpd is None:
        sys.exit(f"Couldn't listen on {args.host}:{args.port}-{args.port + 9}: {last_error}")
    HTTPD = httpd
    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    httpd.check_host = loopback
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/" if loopback else f"http://{args.host}:{port}/"
    dev = DEVCONN.resolve()

    print(f"Tiiny Control Panel {VERSION}")
    print(f"  tiiny CLI : {RT.bin or 'not found (interactive features need it)'}" + (f"  ({RT.cli_version})" if RT.cli_version else ""))
    print(f"  device    : {dev['address'] or 'not set up'}" + (f"  (from {dev['sources']['address']})" if dev["address"] else ""))
    print(f"  API key   : {'found in ' + dev['sources']['key'] if dev['key'] else 'missing (log in from the Account page)'}")
    print(f"  your data : {STORE.root}")
    if port != args.port:
        print(f"  note      : port {args.port} is taken by another program, so the panel uses {port}")
    print(f"  open      : {url}" + ("" if args.no_browser else "  (your browser opens it, signed in)"))
    if args.no_browser:
        print(f"  sign in   : {BROWSER.link(url)}  (works once, for {CODE_TTL // 60} min; run it again for a new link)")
    if ALLOW_COMMANDS:
        print("  commands  : agent mode may run commands and read and write files here (it asks before each one)")
    if not loopback:
        print("  WARNING   : listening beyond this computer; anyone who can reach this address controls the device.")
    print("Press Ctrl+C to stop.")
    sys.stdout.flush()

    METRICS.start()
    selfupdate.start_daily(SETTINGS)
    if SETTINGS.get("lan_enabled") and loopback:
        try:
            lan_start()
            print(f"  phones    : {'https' if TLS else 'http'}://{LAN['ip']}:{LAN['port']}/  (paired devices only)")
        except BadRequest as e:
            print(f"  phones    : not available ({e})")
    if not args.no_browser:
        threading.Timer(0.7, open_signed_in, args=(url,)).start()

    def stop(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down…")
        lan_stop()
        PTYS.kill_all()
        httpd.server_close()
        shutil.rmtree(RT.work, ignore_errors=True)
    if RESTART["wanted"]:  # after an update (System → Tiiny Control Panel): run the new version with the same options
        again = sys.argv[1:] + ([] if "--no-browser" in sys.argv[1:] else ["--no-browser"])  # the open page reconnects
        print("Restarting the panel…")
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable, str(APP_DIR / "tiiny_ui.py"), *again])


if __name__ == "__main__":
    main()
