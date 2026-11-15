"""Optional: read mesh health and per-device signal from an eero account.

eero has no local interface, so this talks to the same cloud service the
eero app uses. That service is not a published API: it can change without
notice, and this module then simply reports an error. Only read requests
are made. The login token is stored in the data folder with owner-only
permissions; delete eero_session.json to sign out.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import ssl

API_HOST = "api-user.e2ro.com"
API_PREFIX = "/2.2"


class EeroError(Exception):
    pass


class EeroClient(object):
    def __init__(self, session_path):
        self.session_path = session_path
        self.token = None
        self.pending = None
        self._load()

    # -- session file
    def _load(self):
        try:
            with open(self.session_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.token = data.get("token")
            self.pending = data.get("pending")
        except (OSError, ValueError):
            pass

    def _save(self):
        folder = os.path.dirname(os.path.abspath(self.session_path))
        os.makedirs(folder, exist_ok=True)
        temp = self.session_path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump({"token": self.token, "pending": self.pending}, handle)
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.session_path)

    def signed_in(self):
        return bool(self.token)

    def sign_out(self):
        self.token = self.pending = None
        try:
            os.remove(self.session_path)
        except OSError:
            pass

    # -- transport
    def _request(self, method, path, body=None, token=None):
        headers = {"User-Agent": "wifiscout", "Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Cookie"] = "s=%s" % token
        if not path.startswith(API_PREFIX):
            path = API_PREFIX + path
        conn = http.client.HTTPSConnection(API_HOST, 443, timeout=15, context=ssl.create_default_context())
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read(8 * 1024 * 1024)
        except (OSError, http.client.HTTPException) as exc:
            raise EeroError("Could not reach the eero service: %s" % exc)
        finally:
            conn.close()
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            raise EeroError("Unexpected reply from the eero service (HTTP %d)." % response.status)
        meta = data.get("meta") or {}
        code = meta.get("code", response.status)
        if code == 401:
            raise EeroError("The eero sign-in has expired. Run 'wifiscout eero --login' again.")
        if code >= 400:
            raise EeroError("eero service error %s: %s" % (code, meta.get("error") or "unknown"))
        return data.get("data")

    # -- sign in (two steps: request a code, then confirm it)
    def start_login(self, identifier):
        data = self._request("POST", "/login", {"login": identifier.strip()})
        token = (data or {}).get("user_token")
        if not token:
            raise EeroError("The eero service did not start a sign-in.")
        self.pending = token
        self._save()

    def finish_login(self, code):
        if not self.pending:
            raise EeroError("Start the sign-in first.")
        self._request("POST", "/login/verify", {"code": code.strip()}, token=self.pending)
        self.token, self.pending = self.pending, None
        self._save()

    # -- reads
    def get(self, path):
        if not self.token:
            raise EeroError("Not signed in to eero.")
        return self._request("GET", path, token=self.token)

    def fetch(self):
        """Download the first network's settings, nodes and clients, normalised."""
        account = self.get("/account") or {}
        networks = ((account.get("networks") or {}).get("data")) or []
        if not networks:
            raise EeroError("No eero network on this account.")
        url = networks[0].get("url") or ""
        match = re.search(r"/networks/(\d+)", url)
        if not match:
            raise EeroError("Could not read the eero network id.")
        base = "/networks/%s" % match.group(1)
        network = self.get(base) or {}
        nodes = self.get(base + "/eeros") or []
        clients = self.get(base + "/devices") or []
        return normalize(network, nodes, clients)


def _dig(data, *path):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _dbm(value):
    match = re.search(r"-\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _band(value):
    text = str(value or "")
    if text.startswith("2"):
        return "2.4"
    if text.startswith("5"):
        return "5"
    if text.startswith("6"):
        return "6"
    return None


def normalize(network, nodes, clients):
    """Reduce the eero replies to the fields the analysis uses."""
    speed = network.get("speed") or {}
    out = {
        "network": {
            "name": network.get("name"),
            "status": _dig(network, "health", "internet", "status") or network.get("status"),
            "isp_up": _dig(network, "health", "internet", "isp_up"),
            "bridge": (_dig(network, "connection", "mode") or "").lower() == "bridge",
            "wan_type": network.get("wan_type") or _dig(network, "lease", "mode"),
            "speed_down": _dig(speed, "down", "value"), "speed_up": _dig(speed, "up", "value"),
            "speed_date": speed.get("date"),
            "band_steering": network.get("band_steering"),
            "sqm": network.get("sqm"),
            "wpa3": network.get("wpa3"),
            "upnp": network.get("upnp"),
            "thread": network.get("thread"),
            "update_available": _dig(network, "updates", "has_update"),
        },
        "nodes": [],
        "clients": [],
    }
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        out["nodes"].append({
            "name": node.get("location") or node.get("model") or "eero",
            "model": node.get("model"),
            "gateway": bool(node.get("gateway")),
            "wired": bool(node.get("wired")),
            "status": node.get("status"),
            "mesh_bars": node.get("mesh_quality_bars"),
            "clients": node.get("connected_clients_count"),
            "ip": node.get("ip_address"),
            "firmware": node.get("os_version") or node.get("os"),
            "update_available": node.get("update_available"),
            "heartbeat_ok": node.get("heartbeat_ok"),
            "bands": node.get("bands") or [],
        })
    for client in clients or []:
        if not isinstance(client, dict):
            continue
        connectivity = client.get("connectivity") or {}
        name = client.get("nickname") or client.get("hostname") or client.get("manufacturer") or client.get("mac")
        frequency = _dig(client, "interface", "frequency") or connectivity.get("frequency")
        rate = _dig(connectivity, "rx_rate_info", "rate_bps")
        out["clients"].append({
            "name": name,
            "mac": (client.get("mac") or "").lower() or None,
            "ip": client.get("ip"),
            "connected": bool(client.get("connected")),
            "wireless": bool(client.get("wireless")),
            "band": _band(frequency) if client.get("wireless") else None,
            "rssi": _dbm(connectivity.get("signal")),
            "score_bars": connectivity.get("score_bars"),
            "node": _dig(client, "source", "location"),
            "channel": client.get("channel"),
            "rx_mbps": round(float(rate) / 1e6, 1) if isinstance(rate, (int, float)) and rate > 0 else None,
            "type": client.get("device_type"),
            "manufacturer": client.get("manufacturer"),
        })
    return out
