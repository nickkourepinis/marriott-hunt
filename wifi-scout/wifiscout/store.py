"""Everything WiFi Scout saves lives in one folder (default ./wifiscout-data)."""
from __future__ import annotations

import json
import os
import re
import threading
import time

from .wifi import guess_band

DEFAULT_FLOORS = ["Main floor"]
SPOT_NUMBER_FIELDS = ("x", "y", "rtt_ms", "jitter_ms", "loss_pct", "down_mbps", "up_mbps", "rssi", "noise",
                      "channel", "tx_rate", "rx_rate", "heading", "steps", "gps_accuracy", "net_down_mbps")
SPOT_TEXT_FIELDS = ("name", "floor", "bssid", "ssid", "standard", "band", "source", "note")
RADIO_FIELDS = ("rssi", "noise", "channel", "tx_rate", "rx_rate", "bssid", "ssid", "standard", "band")
MAX_SPOTS = 500
MAX_PLAN_BYTES = 2 * 1024 * 1024


class DataDir(object):
    def __init__(self, path):
        self.root = os.path.abspath(path)
        os.makedirs(self.root, exist_ok=True)

    def path(self, name):
        return os.path.join(self.root, name)

    def load_json(self, name, default=None):
        try:
            with open(self.path(name), "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return default

    def save_json(self, name, data):
        temp = self.path(name + ".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        os.replace(temp, self.path(name))


def _clean_text(value, limit=60):
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).strip()
    return text[:limit] or None


def _clean_number(value, low=-1e6, high=1e6):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if not match:
            return None
        number = float(match.group(0))
    if number != number or number < low or number > high:
        return None
    return int(number) if number == int(number) and abs(number) < 1e9 else round(number, 3)


def clean_radio(raw):
    """Normalise a Wi-Fi reading from the iPhone Shortcut or manual entry."""
    radio = {}
    aliases = {"ch": "channel", "tx": "tx_rate", "rx": "rx_rate", "std": "standard", "signal": "rssi"}
    for key, value in (raw or {}).items():
        key = aliases.get(str(key).lower(), str(key).lower())
        if key not in RADIO_FIELDS or value in (None, ""):
            continue
        if key in ("rssi", "noise"):
            number = _clean_number(value, -130, 0)
            if number is not None and number < 0:
                radio[key] = number
        elif key == "channel":
            number = _clean_number(value, 1, 233)
            if number is not None:
                radio[key] = int(number)
        elif key in ("tx_rate", "rx_rate"):
            number = _clean_number(value, 0, 50000)
            if number is not None:
                radio[key] = number
        elif key == "bssid":
            text = str(value).strip().lower().replace("-", ":")
            parts = text.split(":")
            if len(parts) == 6 and all(re.fullmatch(r"[0-9a-f]{1,2}", p) for p in parts):
                radio[key] = ":".join(p.zfill(2) for p in parts)
        else:
            radio[key] = _clean_text(value, 40)
    if radio.get("band") not in ("2.4", "5", "6"):
        band = guess_band(radio.get("channel"), radio.get("tx_rate"), radio.get("standard"))
        if band:
            radio["band"] = band
        else:
            radio.pop("band", None)
    return radio


class Survey(object):
    """The room-by-room readings. Thread-safe; every change is written to disk."""

    def __init__(self, datadir):
        self.datadir = datadir
        self.lock = threading.RLock()
        data = datadir.load_json("survey.json", {}) or {}
        self.data = {
            "profile": data.get("profile") or {},
            "floors": data.get("floors") or list(DEFAULT_FLOORS),
            "spots": data.get("spots") or [],
            "plans": data.get("plans") or {},
            "stride_m": data.get("stride_m") or 0.7,
            "version": int(data.get("version") or 0),
        }
        self.pending_radio = {}  # client ip -> (time, radio)
        self.recent_radio = {}   # client ip -> {"t", "radio", "spot_id"}

    def _save(self):
        self.data["version"] += 1
        self.datadir.save_json("survey.json", self.data)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def version(self):
        with self.lock:
            return self.data["version"]

    def set_profile(self, raw):
        profile = {}
        home = _clean_text((raw or {}).get("home"), 20)
        if home in ("house", "apartment", "condo", "townhouse"):
            profile["home"] = home
        for key, low, high in (("floors", 1, 10), ("sqft", 100, 50000), ("plan_down", 1, 100000), ("plan_up", 0.1, 100000)):
            number = _clean_number((raw or {}).get(key), low, high)
            if number is not None:
                profile[key] = number
        with self.lock:
            self.data["profile"] = profile
            self._save()
            return profile

    def set_floors(self, names):
        floors = []
        for name in names or []:
            name = _clean_text(name, 30)
            if name and name not in floors:
                floors.append(name)
        with self.lock:
            used = [s.get("floor") for s in self.data["spots"] if s.get("floor")]
            for name in used:
                if name not in floors:
                    floors.append(name)
            self.data["floors"] = floors[:8] or list(DEFAULT_FLOORS)
            self._save()
            return self.data["floors"]

    def set_stride(self, value):
        number = _clean_number(value, 0.3, 1.2)
        with self.lock:
            if number is not None:
                self.data["stride_m"] = number
                self._save()
            return self.data["stride_m"]

    def set_plan(self, floor, image, width_m):
        floor = _clean_text(floor, 30)
        with self.lock:
            if not floor:
                return False
            if not image:
                self.data["plans"].pop(floor, None)
            else:
                if not isinstance(image, str) or not re.match(r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$", image):
                    return False
                if len(image) > MAX_PLAN_BYTES:
                    return False
                self.data["plans"][floor] = {"image": image, "width_m": _clean_number(width_m, 2, 200) or 12,
                                             "rev": int(time.time() * 1000)}
            self._save()
            return True

    def upsert_spot(self, raw, client_ip=None):
        """Create or update a spot from the phone page. Returns the stored spot."""
        raw = raw or {}
        with self.lock:
            spot_id = _clean_text(raw.get("id"), 24)
            spot = None
            if spot_id:
                spot = next((s for s in self.data["spots"] if s["id"] == spot_id), None)
            if spot is None:
                if len(self.data["spots"]) >= MAX_SPOTS:
                    return None
                spot = {"id": spot_id or "s%d" % int(time.time() * 1000), "ts": time.time(), "client": client_ip}
                self.data["spots"].append(spot)
            for key in SPOT_TEXT_FIELDS:
                if key in raw:
                    spot[key] = _clean_text(raw[key])
            for key in SPOT_NUMBER_FIELDS:
                if key in raw:
                    spot[key] = _clean_number(raw[key])
            radio = clean_radio({key: raw[key] for key in RADIO_FIELDS if key in raw})
            for key in ("rssi", "noise", "channel", "bssid"):
                if key in raw:
                    spot[key] = radio.get(key)
            if "band" in radio and not spot.get("band"):
                spot["band"] = radio["band"]
            if not spot.get("name"):
                spot["name"] = "Spot %d" % len(self.data["spots"])
            if not spot.get("floor"):
                spot["floor"] = self.data["floors"][0]
            if spot["floor"] not in self.data["floors"]:
                self.data["floors"].append(spot["floor"])
            pending = self.pending_radio.get(client_ip)
            if pending and spot.get("rssi") is None and time.time() - pending[0] < 180:
                spot.update(pending[1])
                spot["radio_source"] = "shortcut"
                del self.pending_radio[client_ip]
                if client_ip in self.recent_radio:
                    self.recent_radio[client_ip]["spot_id"] = spot["id"]
            self._save()
            return dict(spot)

    def delete_spot(self, spot_id):
        with self.lock:
            before = len(self.data["spots"])
            self.data["spots"] = [s for s in self.data["spots"] if s["id"] != spot_id]
            if len(self.data["spots"]) != before:
                self._save()
                return True
            return False

    def clear_spots(self):
        with self.lock:
            self.data["spots"] = []
            self._save()

    def add_radio(self, raw, client_ip, name=None):
        """A reading from the iPhone Shortcut.

        One run of the Shortcut may send several requests (signal, then channel,
        then access point...). Requests from the same phone within 30 seconds are
        merged. The reading is attached to the newest spot that phone measured in
        the last three minutes that has no signal yet. With a room name it becomes
        its own spot. Otherwise it waits for the phone's next measurement.
        """
        radio = clean_radio(raw)
        if not radio:
            return None, "no values"
        with self.lock:
            now = time.time()
            recent = self.recent_radio.get(client_ip)
            if recent and now - recent["t"] < 30:
                recent["radio"].update(radio)
                recent["t"] = now
                if recent.get("spot_id"):
                    spot = next((s for s in self.data["spots"] if s["id"] == recent["spot_id"]), None)
                    if spot is not None:
                        spot.update(radio)
                        self._save()
                        return dict(spot), "attached"
            else:
                recent = {"t": now, "radio": dict(radio), "spot_id": None}
                self.recent_radio[client_ip] = recent
            for ip in [ip for ip, item in self.recent_radio.items() if now - item["t"] > 600]:
                del self.recent_radio[ip]
            merged = recent["radio"]
            if "rssi" not in merged:
                return dict(merged), "partial"
            name = _clean_text(name, 40)
            if name:
                spot = {"id": "r%d" % int(now * 1000), "ts": now, "client": client_ip, "name": name,
                        "floor": self.data["floors"][0], "source": "shortcut", "radio_source": "shortcut"}
                spot.update(merged)
                if len(self.data["spots"]) < MAX_SPOTS:
                    self.data["spots"].append(spot)
                    recent["spot_id"] = spot["id"]
                    self._save()
                return dict(spot), "new spot"
            for spot in sorted(self.data["spots"], key=lambda s: -(s.get("ts") or 0)):
                if spot.get("client") == client_ip and spot.get("rssi") is None and now - (spot.get("ts") or 0) < 180:
                    spot.update(merged)
                    spot["radio_source"] = "shortcut"
                    recent["spot_id"] = spot["id"]
                    self._save()
                    return dict(spot), "attached"
            self.pending_radio[client_ip] = (now, merged)
            for ip in [ip for ip, (stamp, _) in self.pending_radio.items() if now - stamp > 600]:
                del self.pending_radio[ip]
            return dict(merged), "waiting"
