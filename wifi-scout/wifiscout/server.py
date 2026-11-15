"""The survey collector: a small web server on the home network.

It serves the phone page, answers the phone's speed tests, receives readings
(from the page and from the iPhone Shortcut) and shows the live report.
Access needs the random key printed at start-up and is limited to devices
on the local network.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import pkgutil
import secrets
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, analyze, audit, qr, store, tlscert

MAX_DOWN_BYTES = 512 * 1024 * 1024
MAX_UP_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 3 * 1024 * 1024
SHORTCUT_NAME = "WiFi Scout"


def asset(name):
    data = pkgutil.get_data("wifiscout", "web/" + name)
    return data.decode("utf-8") if data else ""


def inline_page(name, extra=None):
    """Load a page and inline its stylesheet and scripts so one request delivers everything."""
    html = asset(name)
    for part in ("app.css", "common.js", "m.js", "report.js"):
        marker = "/*INLINE:%s*/" % part
        if marker in html:
            html = html.replace(marker, asset(part))
    for key, value in (extra or {}).items():
        html = html.replace(key, value)
    return html


def json_for_script(data):
    """JSON that is safe inside a <script> element."""
    return json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026") \
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


class App(object):
    """State shared by the HTTP and HTTPS listeners."""

    def __init__(self, datadir, local_ip=None, collector_via="unknown", token=None):
        self.datadir = datadir
        self.survey = store.Survey(datadir)
        self.token = token or secrets.token_urlsafe(6)
        self.local_ip = local_ip or "127.0.0.1"
        self.collector_via = collector_via
        self.http_port = None
        self.https_port = None
        self.block = os.urandom(256 * 1024)
        self._cache = (None, None)
        self._lock = threading.Lock()
        self.plan_rev = {}

    # -- addresses
    def url(self, secure, path="/m", host=None, with_key=True):
        port = self.https_port if secure else self.http_port
        if port is None:
            return None
        return "%s://%s:%d%s%s" % ("https" if secure else "http", host or self.local_ip, port, path,
                                   ("?k=" + self.token) if with_key else "")

    def phone_url(self):
        return self.url(True) or self.url(False)

    def radio_url(self):
        return self.url(False, "/api/radio") or self.url(True, "/api/radio")

    # -- data
    def _stamp(self):
        stamps = [self.survey.version()]
        for name in ("audit.json", "eero.json", "monitor.jsonl"):
            try:
                stamps.append(os.path.getmtime(self.datadir.path(name)))
            except OSError:
                stamps.append(0)
        return tuple(stamps)

    def bundle(self):
        stamp = self._stamp()
        with self._lock:
            if self._cache[0] == stamp:
                return self._cache[1]
        bundle = audit.build_bundle(self.datadir, survey_data=self.survey.snapshot(), collector_via=self.collector_via)
        for plan in bundle["survey"]["plans"].values():
            plan.pop("image", None)  # pictures travel separately
        with self._lock:
            self._cache = (stamp, bundle)
        return bundle

    def state(self, client_ip=None):
        data = self.survey.snapshot()
        scored = []
        for spot in data["spots"]:
            item = dict(spot)
            item.pop("client", None)
            item["scores"] = analyze.spot_scores(spot, self.collector_via)
            scored.append(item)
        plans = {}
        for floor, plan in data["plans"].items():
            plans[floor] = {"width_m": plan.get("width_m"), "rev": plan.get("rev") or len(plan.get("image") or "")}
        return {
            "version": data["version"], "floors": data["floors"], "spots": scored, "profile": data["profile"],
            "stride_m": data["stride_m"], "plans": plans,
            "server": {
                "version": __version__, "collector_via": self.collector_via, "shortcut_name": SHORTCUT_NAME,
                "https_url": self.url(True), "http_url": self.url(False), "radio_url": self.radio_url(),
            },
        }

    def live_info(self):
        phone = self.phone_url()
        return {
            "phone_url": phone, "phone_url_plain": self.url(False), "radio_url": self.radio_url(),
            "qr_svg": qr.to_svg(qr.encode(phone), scale=6) if phone else None,
            "secure": self.https_port is not None, "shortcut_name": SHORTCUT_NAME, "key": self.token,
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WiFiScout/" + __version__
    sys_version = ""
    timeout = 30
    # Send each reply at once. Without this, small replies stall about 40 ms waiting for the
    # phone to acknowledge the headers, which would wreck the response-time readings.
    disable_nagle_algorithm = True
    wbufsize = 64 * 1024

    # -- plumbing
    def setup(self):
        BaseHTTPRequestHandler.setup(self)
        if isinstance(self.connection, ssl.SSLSocket):
            self.connection.do_handshake()

    def log_message(self, format, *args):
        pass

    @property
    def app(self):
        return self.server.app

    def _local_client(self):
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return address.is_private or address.is_loopback or address.is_link_local

    def _authorized(self, query):
        supplied = self.headers.get("X-WS-Key") or (query.get("k") or [""])[0]
        return bool(supplied) and hmac.compare_digest(supplied.encode("utf-8"), self.app.token.encode("utf-8"))

    def _send(self, status, body, content_type, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status=200):
        self._send(status, json.dumps(data), "application/json")

    def _html(self, html, status=200):
        self._send(status, html, "text/html; charset=utf-8", {
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
                                       "base-uri 'none'; form-action 'none'",
        })

    def _denied(self):
        self._html("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                   "<title>WiFi Scout</title><body style='font:16px system-ui;margin:40px;max-width:36em'>"
                   "<h1>Link needed</h1><p>Open the full link printed in the WiFi Scout window, or scan its QR code. "
                   "The link carries a key that keeps other devices out.</p>", 403)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_JSON_BYTES:
            self.close_connection = True
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _drain(self):
        """Discard a request body we are not going to use, to keep the connection usable."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_UP_BYTES:
            self.close_connection = True
            return
        while length > 0:
            chunk = self.rfile.read(min(length, 262144))
            if not chunk:
                break
            length -= len(chunk)

    # -- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._local_client():
            self.close_connection = True
            return self._send(403, "local network only", "text/plain")
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path == "/m":
            return self._html(inline_page("m.html"))
        if path in ("/", "/report"):
            if not self._authorized(query):
                return self._denied()
            from . import report
            return self._html(report.render(self.app.bundle(), live=self.app.live_info()))
        if not path.startswith("/api/"):
            return self._send(404, "not found", "text/plain")
        if not self._authorized(query):
            return self._json({"error": "key required"}, 403)
        if path == "/api/ping":
            return self._json({"t": int(time.time() * 1000)})
        if path == "/api/down":
            return self._down(query)
        if path == "/api/state":
            return self._json(self.app.state(self.client_address[0]))
        if path == "/api/plan":
            floor = (query.get("floor") or [""])[0]
            plan = self.app.survey.snapshot()["plans"].get(floor)
            return self._json(plan or {}, 200 if plan else 404)
        if path == "/api/analysis":
            return self._json(self.app.bundle()["analysis"])
        if path == "/api/bundle":
            bundle = self.app.bundle()
            return self._json({"bundle": bundle, "live": self.app.live_info()})
        if path == "/api/radio":
            return self._radio({key: values[0] for key, values in query.items()})
        return self._json({"error": "unknown"}, 404)

    def do_POST(self):
        if not self._local_client():
            self.close_connection = True
            return self._send(403, "local network only", "text/plain")
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path
        if not self._authorized(query):
            self._drain()
            return self._json({"error": "key required"}, 403)
        if path == "/api/up":
            return self._up()
        if path == "/api/radio":
            body = self._read_json()
            merged = {key: values[0] for key, values in query.items()}
            merged.update(body or {})
            return self._radio(merged)
        body = self._read_json()
        if body is None:
            return self._json({"error": "bad request"}, 400)
        survey = self.app.survey
        if path == "/api/spot":
            spot = survey.upsert_spot(body, self.client_address[0])
            if spot is None:
                return self._json({"error": "too many spots"}, 400)
            spot.pop("client", None)
            spot["scores"] = analyze.spot_scores(spot, self.app.collector_via)
            return self._json({"spot": spot})
        if path == "/api/spot/delete":
            return self._json({"deleted": survey.delete_spot(str(body.get("id")))})
        if path == "/api/profile":
            return self._json({"profile": survey.set_profile(body)})
        if path == "/api/floors":
            floors = body.get("floors")
            return self._json({"floors": survey.set_floors(floors if isinstance(floors, list) else [])})
        if path == "/api/settings":
            return self._json({"stride_m": survey.set_stride(body.get("stride_m"))})
        if path == "/api/plan":
            ok = survey.set_plan(body.get("floor"), body.get("image"), body.get("width_m"))
            return self._json({"ok": ok}, 200 if ok else 400)
        return self._json({"error": "unknown"}, 404)

    # -- speed test endpoints
    def _down(self, query):
        try:
            total = int((query.get("bytes") or ["1000000"])[0])
        except ValueError:
            total = 1000000
        total = max(0, min(total, MAX_DOWN_BYTES))
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        block = self.app.block
        remaining = total
        deadline = time.time() + 20
        try:
            while remaining > 0 and time.time() < deadline:
                chunk = block if remaining >= len(block) else block[:remaining]
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except (OSError, ssl.SSLError):
            remaining = -1
        if remaining != 0:
            self.close_connection = True

    def _up(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_UP_BYTES:
            self.close_connection = True
            return self._json({"error": "too large"}, 413)
        started = time.time()
        received = 0
        while received < length:
            chunk = self.rfile.read(min(length - received, 262144))
            if not chunk:
                break
            received += len(chunk)
        return self._json({"bytes": received, "ms": round((time.time() - started) * 1000, 1)})

    def _radio(self, params):
        name = params.pop("name", None)
        params.pop("k", None)
        result, outcome = self.app.survey.add_radio(params, self.client_address[0], name=name)
        if result is None:
            return self._send(400, "WiFi Scout: no values received. The address should end with rssi= and the RSSI value.",
                              "text/plain; charset=utf-8")
        rssi = result.get("rssi")
        if outcome == "partial":
            return self._send(200, "WiFi Scout: received. Waiting for the signal (rssi) value.", "text/plain; charset=utf-8")
        if outcome == "attached":
            message = "WiFi Scout: %s dBm saved to %s." % (rssi, result.get("name") or "the last spot")
        elif outcome == "new spot":
            message = "WiFi Scout: %s dBm saved as %s." % (rssi, result.get("name"))
        else:
            message = "WiFi Scout: %s dBm received. It will be added to your next measurement." % rssi
        return self._send(200, message, "text/plain; charset=utf-8")


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Dropped connections and refused certificates are routine on a phone: not worth a traceback.
        if os.environ.get("WIFISCOUT_DEBUG"):
            ThreadingHTTPServer.handle_error(self, request, client_address)


def _bind(app, port, tries=8):
    for candidate in range(port, port + tries):
        try:
            server = QuietServer(("0.0.0.0", candidate), Handler)
        except OSError:
            continue
        server.app = app
        return server
    return None


def start(datadir, local_ip=None, collector_via="unknown", http_port=8787, https_port=8788, tls=True, token=None):
    """Start the HTTP (and, when a certificate can be made, HTTPS) listeners on background threads.

    Returns (app, [servers]). Call server.shutdown() on each to stop.
    """
    app = App(datadir, local_ip=local_ip, collector_via=collector_via, token=token)
    servers = []
    plain = _bind(app, http_port)
    if plain is None:
        raise OSError("no free port near %d" % http_port)
    app.http_port = plain.server_address[1]
    servers.append(plain)
    if tls:
        hosts = [app.local_ip, "127.0.0.1", "localhost"]
        name = socket.gethostname()
        if name and "." not in name:
            hosts += [name, name + ".local"]
        elif name:
            hosts.append(name)
        made = tlscert.ensure(datadir.path("tls"), hosts)
        if made:
            secure = _bind(app, https_port if https_port != app.http_port else https_port + 1)
            if secure is not None:
                try:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.load_cert_chain(made[0], made[1])
                    secure.socket = context.wrap_socket(secure.socket, server_side=True, do_handshake_on_connect=False)
                    app.https_port = secure.server_address[1]
                    servers.append(secure)
                except (ssl.SSLError, OSError):
                    secure.server_close()
    for server in servers:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.25})
        thread.daemon = True
        thread.start()
    return app, servers
