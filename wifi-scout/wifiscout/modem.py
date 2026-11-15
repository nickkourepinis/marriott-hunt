"""Modem line quality: cable (DOCSIS) signal levels and 5G/LTE gateway signal.

Cable modems publish their signal table on a status page, usually at
http://192.168.100.1. Layouts differ by maker, so parsing is heuristic and
there is a fallback: save the page from a browser and pass it with --file.
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import time
import urllib.request
from html.parser import HTMLParser

MODEM_ADDRESS = "192.168.100.1"
STATUS_PATHS = (
    "/cmconnectionstatus.html", "/cgi-bin/status", "/MotoConnection.asp", "/DocsisStatus.htm", "/DocsisStatus.asp",
    "/RgConnect.asp", "/cmSignalData.htm", "/indexData.htm", "/data/dsinfo.asp", "/cgi-bin/status_cgi", "/",
)


class _TableParser(HTMLParser):
    """Collects every table as rows of cell text. Tolerates nesting and missing end tags."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.tables = []
        self.title = ""
        self._stack = []
        self._in_title = False
        self._skip = 0

    def _close_cell(self, frame):
        if frame["cell"] is not None and frame["row"] is not None:
            frame["row"].append(re.sub(r"\s+", " ", "".join(frame["cell"])).strip())
        frame["cell"] = None

    def _close_row(self, frame):
        self._close_cell(frame)
        if frame["row"]:
            frame["rows"].append(frame["row"])
        frame["row"] = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None})
        elif self._stack:
            frame = self._stack[-1]
            if tag == "tr":
                self._close_row(frame)
                frame["row"] = []
            elif tag in ("td", "th"):
                if frame["row"] is None:
                    frame["row"] = []
                self._close_cell(frame)
                frame["cell"] = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif self._stack:
            frame = self._stack[-1]
            if tag in ("td", "th"):
                self._close_cell(frame)
            elif tag == "tr":
                self._close_row(frame)
            elif tag == "table":
                self._close_row(frame)
                self._stack.pop()
                if frame["rows"]:
                    self.tables.append(frame["rows"])

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        elif self._stack and self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"].append(data)

    def close(self):
        HTMLParser.close(self)
        while self._stack:
            frame = self._stack.pop()
            self._close_row(frame)
            if frame["rows"]:
                self.tables.append(frame["rows"])


def parse_tables(html):
    parser = _TableParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.tables, parser.title.strip()


def _number(text):
    match = re.search(r"-?\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(match.group(0)) if match else None


def _frequency_mhz(text):
    value = _number(text)
    if value is None:
        return None
    low = str(text).lower()
    if "khz" in low:
        return round(value / 1000.0, 3)
    if "mhz" in low:
        return value
    if value > 100000:
        return round(value / 1e6, 3)
    return value


def _field_for(header):
    text = header.lower()
    if "uncorrect" in text or "uncorr" in text:
        return "uncorrected"
    if "correct" in text:
        return "corrected"
    if "snr" in text or "mer" in text or "signal to noise" in text:
        return "snr"
    if "power" in text or "pwr" in text:
        return "power"
    if "freq" in text:
        return "frequency"
    if "modulation" in text or "channel type" in text:
        return "modulation"
    if "lock" in text or text.strip() == "status":
        return "lock"
    if "symbol" in text or "width" in text:
        return "width"
    if "channel id" in text or text.strip() in ("channel", "ch", "channel id", "id"):
        return "channel"
    return None


def _header_score(cells):
    fields = {_field_for(cell) for cell in cells}
    fields.discard(None)
    return len(fields)


def _channels_from_table(table):
    """Return (direction, channels) for one HTML table, or (None, [])."""
    title = ""
    rows = [row for row in table if any(cell for cell in row)]
    if not rows:
        return None, []
    if len(rows[0]) == 1:
        title = rows[0][0].lower()
        rows = rows[1:]
    if len(rows) < 2:
        return None, []
    width = max(len(row) for row in rows)
    first_column = [row[0] for row in rows if row]
    transposed = _header_score(first_column) >= 2 and _header_score(rows[0]) < 2 and width >= 2
    if transposed:
        columns = []
        for index in range(width):
            columns.append([row[index] if index < len(row) else "" for row in rows])
        rows = columns
    header_index = None
    for index, row in enumerate(rows[:3]):
        if _header_score(row) >= 2:
            header_index = index
            break
    if header_index is None:
        return None, []
    if header_index > 0 and not title:
        title = " ".join(rows[header_index - 1]).lower()
    header = [_field_for(cell) for cell in rows[header_index]]
    channels = []
    for row in rows[header_index + 1:]:
        if len(row) < 2:
            continue
        item = {}
        for field, cell in zip(header, row):
            if field is None or cell == "":
                continue
            if field in ("power", "snr", "corrected", "uncorrected"):
                item[field] = _number(cell)
            elif field == "frequency":
                item[field] = _frequency_mhz(cell)
            else:
                item.setdefault(field, cell)
        lock = str(item.get("lock", "locked")).lower()
        if "not" in lock or "unlock" in lock:
            continue
        if item.get("power") is None and item.get("snr") is None:
            continue
        if item.get("frequency") in (0, 0.0) and not item.get("power"):
            continue
        channels.append(item)
    if not channels:
        return None, []
    has_snr = any(c.get("snr") is not None for c in channels)
    has_errors = any("corrected" in c or "uncorrected" in c for c in channels)
    if "upstream" in title:
        direction = "up"
    elif "downstream" in title:
        direction = "down"
    elif has_snr or has_errors:
        direction = "down"
    else:
        direction = "up"
    if direction == "up":
        for channel in channels:
            channel.pop("snr", None)
    return direction, channels


def parse_html_tables(html):
    """Generic status page -> (downstream, upstream, title)."""
    tables, title = parse_tables(html)
    down, up = [], []
    for table in tables:
        direction, channels = _channels_from_table(table)
        if direction == "down":
            down.extend(channels)
        elif direction == "up":
            up.extend(channels)
    return down, up, title


def parse_netgear_script(html):
    """Netgear CM-series pages keep the table in JavaScript strings -> (downstream, upstream)."""
    text = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    down, up = [], []
    for name, body in re.findall(r"function\s+(Init\w*TableTagValue)\s*\(\s*\)\s*\{(.*?)\n\}", text, flags=re.S):
        match = re.search(r"tagValueList\s*=\s*['\"]([^'\"]*)['\"]", body)
        if not match:
            continue
        tokens = match.group(1).split("|")
        try:
            count = int(tokens[0])
        except ValueError:
            continue
        if count <= 0:
            continue
        stride = (len(tokens) - 1) // count
        lowered = name.lower()
        for index in range(count):
            part = tokens[1 + index * stride: 1 + (index + 1) * stride]
            if len(part) < 7 or "lock" not in part[1].lower() or "not" in part[1].lower():
                continue
            if "ds" in lowered and "ofdm" not in lowered and len(part) >= 9:
                down.append({"channel": part[3], "modulation": part[2], "frequency": _frequency_mhz(part[4]),
                             "power": _number(part[5]), "snr": _number(part[6]),
                             "corrected": _number(part[7]), "uncorrected": _number(part[8])})
            elif "ds" in lowered and "ofdm" in lowered and len(part) >= 7:
                down.append({"channel": part[3], "modulation": "OFDM", "frequency": _frequency_mhz(part[4]),
                             "power": _number(part[5]), "snr": _number(part[6]),
                             "uncorrected": _number(part[-1]) if len(part) >= 11 else None})
            elif "us" in lowered and "ofdm" not in lowered:
                up.append({"channel": part[3], "modulation": part[2], "frequency": _frequency_mhz(part[5]),
                           "power": _number(part[6])})
            elif "us" in lowered and len(part) >= 6:
                up.append({"channel": part[3], "modulation": "OFDMA", "frequency": _frequency_mhz(part[4]),
                           "power": _number(part[5])})
    return down, up


def parse_json_channels(text):
    """Hitron-style JSON channel lists -> (downstream, upstream)."""
    try:
        data = json.loads(text)
    except ValueError:
        return [], []
    if not isinstance(data, list):
        return [], []
    down, up = [], []
    for row in data:
        if not isinstance(row, dict) or "signalStrength" not in row:
            continue
        item = {"channel": row.get("channelId"), "frequency": _frequency_mhz(row.get("frequency", "")),
                "power": _number(row.get("signalStrength"))}
        if "snr" in row:
            item["snr"] = _number(row.get("snr"))
            item["corrected"] = _number(row.get("correcteds", ""))
            item["uncorrected"] = _number(row.get("uncorrect", ""))
            down.append(item)
        else:
            up.append(item)
    return down, up


def parse_cellular(text):
    """T-Mobile style 5G gateway JSON -> {"5g": {...}, "4g": {...}} or None."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    signal = data.get("signal") if isinstance(data, dict) else None
    if not isinstance(signal, dict):
        return None
    out = {}
    for key in ("5g", "4g"):
        radio = signal.get(key)
        if isinstance(radio, dict) and radio.get("rsrp") is not None:
            out[key] = {"rsrp": radio.get("rsrp"), "rsrq": radio.get("rsrq"), "sinr": radio.get("sinr"),
                        "bars": radio.get("bars"), "bands": radio.get("bands") or []}
    if not out:
        return None
    device = data.get("device") if isinstance(data.get("device"), dict) else {}
    out["model"] = " ".join(str(device.get(k)) for k in ("manufacturer", "model") if device.get(k)) or None
    return out


def summarize(down, up):
    """Collapse per-channel readings into the numbers that matter."""
    def values(rows, key):
        return [row[key] for row in rows if isinstance(row.get(key), (int, float))]

    powers = values(down, "power")
    snrs = [v for v in values(down, "snr") if v > 0]
    up_powers = values(up, "power")
    modulations = " ".join(str(row.get("modulation", "")) for row in down + up).lower()
    ofdm = "ofdm" in modulations or "other" in modulations or "4096" in modulations
    summary = {
        "downstream_channels": len(down), "upstream_channels": len(up),
        "down_power_min": min(powers) if powers else None, "down_power_max": max(powers) if powers else None,
        "down_power_avg": round(sum(powers) / len(powers), 1) if powers else None,
        "snr_min": min(snrs) if snrs else None, "snr_avg": round(sum(snrs) / len(snrs), 1) if snrs else None,
        "up_power_min": min(up_powers) if up_powers else None, "up_power_max": max(up_powers) if up_powers else None,
        "corrected": int(sum(values(down, "corrected"))), "uncorrected": int(sum(values(down, "uncorrected"))),
        "docsis": None,
    }
    if down:
        summary["docsis"] = "3.1" if ofdm else ("3.0" if len(down) >= 4 else "2.0 or 3.0")
    return summary


def parse_page(text):
    """Try every known layout on one page. Returns a modem result dict."""
    result = {"status": "no_data", "kind": None, "model": None, "downstream": [], "upstream": [],
              "summary": None, "cellular": None}
    stripped = (text or "").lstrip()
    if not stripped:
        return result
    if stripped.startswith("{"):
        cellular = parse_cellular(stripped)
        if cellular:
            result.update({"status": "ok", "kind": "cellular", "cellular": cellular, "model": cellular.pop("model", None)})
            return result
    down, up = [], []
    title = ""
    if stripped.startswith("["):
        down, up = parse_json_channels(stripped)
    if not down and not up:
        down, up, title = parse_html_tables(text)
    if not down and not up and "tagValueList" in text:
        down, up = parse_netgear_script(text)
    if down or up:
        result.update({"status": "ok", "kind": "cable", "downstream": down, "upstream": up,
                       "summary": summarize(down, up), "model": title[:60] or None})
        return result
    lowered = text.lower()
    if re.search(r"type=[\"']?password|login|sign in|hnap", lowered):
        result["status"] = "login_required"
        result["model"] = (parse_tables(text)[1] or "")[:60] or None
    return result


def _reachable(address, ports=(80, 443), timeout=0.8):
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            if sock.connect_ex((address, port)) == 0:
                return port
        except OSError:
            pass
        finally:
            sock.close()
    return None


def _get(url, timeout=4.0):
    """Fetch a page from a device on the home network.

    Modem status pages use self-signed certificates, so certificate checks
    are skipped here. Nothing secret is sent: it is an anonymous GET.
    """
    handlers = [urllib.request.ProxyHandler({})]
    if url.startswith("https"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=context))
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (wifiscout)"})
        with urllib.request.build_opener(*handlers).open(request, timeout=timeout) as response:
            return response.read(1024 * 1024).decode("utf-8", errors="replace")
    except Exception:
        return ""


def probe(gateway=None, addresses=None, budget=20.0):
    """Look for a modem status page on the usual addresses."""
    result = {"status": "unreachable", "address": None, "url": None, "kind": None, "model": None,
              "downstream": [], "upstream": [], "summary": None, "cellular": None}
    candidates = list(addresses or [MODEM_ADDRESS])
    best = None
    give_up_at = time.time() + budget
    for address in candidates:
        port = _reachable(address)
        if port is None:
            continue
        scheme = "https" if port == 443 else "http"
        for path in STATUS_PATHS:
            if time.time() > give_up_at:
                break
            url = "%s://%s%s" % (scheme, address, path)
            page = parse_page(_get(url))
            page.update({"address": address, "url": url})
            if page["status"] == "ok":
                return page
            if best is None or (page["status"] == "login_required" and best["status"] != "login_required"):
                best = page
    if gateway:
        text = _get("http://%s/TMI/v1/gateway?get=all" % gateway, timeout=3.0)
        page = parse_page(text) if text.lstrip().startswith("{") else None
        if page and page["status"] == "ok":
            page.update({"address": gateway, "url": "http://%s/TMI/v1/gateway?get=all" % gateway})
            return page
    if best is not None:
        result.update(best)
    return result


def parse_file(path):
    """Parse a status page the user saved from their browser."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        page = parse_page(handle.read(4 * 1024 * 1024))
    page.update({"address": None, "url": "file"})
    return page
