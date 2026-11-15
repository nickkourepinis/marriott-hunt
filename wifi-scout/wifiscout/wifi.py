"""This computer's Wi-Fi link and the networks it can hear.

Supports macOS (system_profiler, legacy airport), Windows (netsh) and Linux
(nmcli, iw). All parsers take raw text so they can be tested offline.
"""
from __future__ import annotations

import json
import os
import re

from . import osutil
from .netinfo import normalize_mac

AIRPORT = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"


def band_of(channel=None, hint=None, freq=None):
    """Return '2.4', '5' or '6' (GHz) from whatever is known."""
    if freq:
        if 2400 <= freq <= 2500:
            return "2.4"
        if 5150 <= freq <= 5900:
            return "5"
        if 5925 <= freq <= 7125:
            return "6"
    if hint:
        text = str(hint).lower().replace(" ", "")
        if text.startswith("6") or "6ghz" in text:
            return "6"
        if text.startswith("5") or "5ghz" in text:
            return "5"
        if text.startswith("2") or "2ghz" in text or "2.4" in text:
            return "2.4"
    if channel is None:
        return None
    if 1 <= channel <= 14:
        return "2.4"
    if 32 <= channel <= 177:
        return "5"
    return None


def channel_from_freq(freq):
    if not freq:
        return None
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5160 <= freq <= 5885:
        return (freq - 5000) // 5
    if 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return None


_FIVE_GHZ = set(range(36, 65, 4)) | set(range(100, 145, 4)) | {149, 153, 157, 161, 165}


def guess_band(channel, tx_rate=None, standard=None):
    """Band from a bare channel number, as an iPhone reports it.

    6 GHz reuses the numbers 1-233, so channel alone is ambiguous: a modern
    link far faster than 2.4 GHz allows, or a number 5 GHz does not use, means 6 GHz.
    """
    if channel is None:
        return None
    modern = any(tag in str(standard or "").lower() for tag in ("ax", "be", "6e"))
    if channel <= 14:
        return "6" if (modern and (tx_rate or 0) > 600) else "2.4"
    if channel in _FIVE_GHZ:
        return "5"
    if channel % 4 == 1 and channel <= 233:
        return "6"
    return "5" if 32 <= channel <= 177 else None


def wifi_generation(phy, band=None):
    """'802.11ax' -> 'Wi-Fi 6' (or 6E on the 6 GHz band)."""
    if not phy:
        return None
    text = str(phy).lower().replace("802.11", "").replace(" ", "")
    modes = set(re.split(r"[/,]", text))
    if "be" in modes:
        return "Wi-Fi 7"
    if "ax" in modes:
        return "Wi-Fi 6E" if band == "6" else "Wi-Fi 6"
    if "ac" in modes:
        return "Wi-Fi 5"
    if "n" in modes:
        return "Wi-Fi 4"
    if modes & {"a", "b", "g"}:
        return "Wi-Fi 3 or older"
    return None


def quality_to_dbm(percent):
    """Windows and NetworkManager report 0-100 quality; this is the usual mapping."""
    return int(round(max(0, min(100, percent)) / 2.0 - 100))


def _int(text):
    match = re.search(r"-?\d+", str(text))
    return int(match.group(0)) if match else None


def _clean_ssid(value):
    if value is None:
        return None
    value = str(value)
    if not value or value.strip().lower() in ("<redacted>", "<hidden>", "--"):
        return None
    return value


def _new_link():
    return {
        "connected": False, "iface": None, "ssid": None, "bssid": None, "rssi": None, "noise": None,
        "snr": None, "channel": None, "band": None, "width": None, "phy": None, "generation": None,
        "tx_rate": None, "rx_rate": None, "security": None, "source": None, "notes": [],
    }


def _finish_link(link):
    if link["rssi"] is not None and link["noise"] is not None and link["noise"] < -1 and link["noise"] > -120:
        link["snr"] = link["rssi"] - link["noise"]
    if link["band"] is None:
        link["band"] = band_of(link["channel"])
    link["generation"] = wifi_generation(link["phy"], link["band"])
    return link


def _neighbor(ssid=None, bssid=None, rssi=None, channel=None, band=None, width=None, phy=None,
              security=None, noise=None, utilization=None):
    return {
        "ssid": _clean_ssid(ssid), "bssid": bssid, "rssi": rssi, "noise": noise, "channel": channel,
        "band": band or band_of(channel), "width": width, "phy": phy, "security": security,
        "utilization": utilization, "mine": False,
    }


# ------------------------------------------------------------------ macOS

def _mac_channel(text):
    """'149 (5GHz, 80MHz)', '36,+1', '11' -> (channel, band, width)."""
    if text is None:
        return None, None, None
    text = str(text)
    channel = _int(text)
    band_match = re.search(r"(\d)\s*GHz", text)
    width_match = re.search(r"(\d+)\s*MHz", text)
    width = int(width_match.group(1)) if width_match else None
    if width is None:
        legacy = re.match(r"\s*\d+\s*,\s*([+-]?\d+)", text)
        if legacy:
            value = legacy.group(1)
            width = 40 if value in ("+1", "-1", "1") else _int(value)
    band = band_of(channel, hint=band_match.group(1) if band_match else None)
    return channel, band, width


def _mac_signal(text):
    """'-53 dBm / -95 dBm' -> (rssi, noise)."""
    values = re.findall(r"-?\d+", str(text or ""))
    rssi = int(values[0]) if values else None
    noise = int(values[1]) if len(values) > 1 else None
    return rssi, noise


def _mac_security(text):
    if not text:
        return None
    value = str(text).replace("spairport_security_mode_", "").replace("_", " ").strip()
    table = {
        "none": "Open", "wep": "WEP", "wpa personal": "WPA Personal", "wpa2 personal": "WPA2 Personal",
        "wpa3 personal": "WPA3 Personal", "wpa3 sae": "WPA3 Personal", "wpa3 transition": "WPA2/WPA3 Personal",
        "wpa2 personal mixed": "WPA/WPA2 Personal", "wpa personal mixed": "WPA/WPA2 Personal",
        "wpa2 enterprise": "WPA2 Enterprise", "wpa3 enterprise": "WPA3 Enterprise", "owe": "Enhanced Open",
    }
    return table.get(value.lower(), value.upper() if len(value) <= 4 else value.title())


def parse_system_profiler(text):
    """macOS `system_profiler -json SPAirPortDataType` -> (link, neighbors)."""
    link = _new_link()
    link["source"] = "system_profiler"
    neighbors = []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return link, neighbors
    sections = data.get("SPAirPortDataType") or []
    for section in sections:
        for iface in section.get("spairport_airport_interfaces") or []:
            current = iface.get("spairport_current_network_information")
            if current and not link["connected"]:
                link["connected"] = True
                link["iface"] = iface.get("_name")
                link["ssid"] = _clean_ssid(current.get("_name"))
                if link["ssid"] is None:
                    link["notes"].append("ssid_hidden")
                link["channel"], link["band"], link["width"] = _mac_channel(current.get("spairport_network_channel"))
                link["rssi"], link["noise"] = _mac_signal(current.get("spairport_signal_noise"))
                link["phy"] = current.get("spairport_network_phymode")
                link["tx_rate"] = _int(current.get("spairport_network_rate")) if current.get("spairport_network_rate") is not None else None
                link["security"] = _mac_security(current.get("spairport_security_mode"))
                bssid = current.get("spairport_network_bssid")
                link["bssid"] = normalize_mac(bssid) if bssid else None
            elif link["iface"] is None and iface.get("_name"):
                link["iface"] = iface.get("_name")
            for key, value in iface.items():
                if not key.endswith("local_wireless_networks") or not isinstance(value, list):
                    continue
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    channel, band, width = _mac_channel(item.get("spairport_network_channel"))
                    rssi, noise = _mac_signal(item.get("spairport_signal_noise"))
                    bssid = item.get("spairport_network_bssid")
                    neighbors.append(_neighbor(
                        ssid=item.get("_name"), bssid=normalize_mac(bssid) if bssid else None, rssi=rssi,
                        noise=noise, channel=channel, band=band, width=width,
                        phy=item.get("spairport_network_phymode"),
                        security=_mac_security(item.get("spairport_security_mode")),
                    ))
    return _finish_link(link), neighbors


def parse_airport_info(text):
    """Legacy macOS `airport -I` -> link."""
    link = _new_link()
    link["source"] = "airport"
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip().lower()] = value.strip()
    if "agrctlrssi" not in values or values.get("state") in ("init", "off"):
        return link
    link["connected"] = bool(values.get("ssid")) or values.get("state") == "running"
    link["ssid"] = _clean_ssid(values.get("ssid"))
    link["bssid"] = normalize_mac(values.get("bssid"))
    link["rssi"] = _int(values.get("agrctlrssi"))
    link["noise"] = _int(values.get("agrctlnoise"))
    link["tx_rate"] = _int(values.get("lasttxrate"))
    link["channel"], link["band"], link["width"] = _mac_channel(values.get("channel"))
    auth = values.get("link auth", "")
    if auth:
        link["security"] = {"none": "Open", "wpa2-psk": "WPA2 Personal", "wpa-psk": "WPA Personal",
                            "wpa3-sae": "WPA3 Personal", "wep": "WEP"}.get(auth.lower(), auth.upper())
    if link["rssi"] == 0:
        link["connected"] = False
    return _finish_link(link)


def parse_airport_scan(text):
    """Legacy macOS `airport -s` -> neighbors."""
    neighbors = []
    for line in text.splitlines():
        match = re.match(
            r"^(.*?)\s((?:[0-9a-fA-F]{1,2}:){5}[0-9a-fA-F]{1,2})\s+(-?\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.*)$", line)
        if not match:
            continue
        channel, band, width = _mac_channel(match.group(4))
        security = match.group(7).strip()
        if security.upper().startswith("NONE"):
            security = "Open"
        else:
            security = re.sub(r"\(.*?\)", "", security).strip()
        neighbors.append(_neighbor(
            ssid=match.group(1).strip(), bssid=normalize_mac(match.group(2)), rssi=int(match.group(3)),
            channel=channel, band=band, width=width, security=security))
    return neighbors


# ---------------------------------------------------------------- Windows

def _netsh_pairs(text):
    """Yield (indent, key, value) for 'key : value' lines of netsh output."""
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        if not key.strip():
            continue
        indent = len(raw) - len(raw.lstrip())
        yield indent, key.strip(), value.strip()


_NAME_KEYS = ("name", "nom", "nombre", "nome")
_AUTH_KEYS = ("authentication", "authentifizierung", "authentification", "autenticaci")
_CHANNEL_KEYS = ("channel", "kanal", "canal")
_BAND_KEYS = ("band", "bande", "banda")


def parse_netsh_interfaces(text):
    """Windows `netsh wlan show interfaces` -> link (the connected adapter if any)."""
    blocks = []
    for indent, key, value in _netsh_pairs(text):
        low = key.lower()
        if low in _NAME_KEYS or not blocks:
            blocks.append([])
        blocks[-1].append((low, value))
    best = None
    for pairs in blocks:
        link = _new_link()
        link["source"] = "netsh"
        rates = []
        quality = None
        for key, value in pairs:
            if key in _NAME_KEYS:
                link["iface"] = value
            elif key == "ssid":
                link["ssid"] = _clean_ssid(value)
            elif "bssid" in key:
                link["bssid"] = normalize_mac(value)
            elif key == "rssi":
                link["rssi"] = _int(value)
            elif re.fullmatch(r"\d+\s*%", value):
                if quality is None:
                    quality = _int(value)
            elif key.startswith(_CHANNEL_KEYS) and "util" not in key:
                link["channel"] = _int(value)
            elif key.startswith(_BAND_KEYS):
                link["band"] = band_of(None, hint=value)
            elif "802.11" in value and link["phy"] is None:
                link["phy"] = value
            elif "mbps" in key or "mbit" in key:
                rates.append(_int(value))
            elif key.startswith(_AUTH_KEYS):
                link["security"] = value
        if link["rssi"] is None and quality is not None:
            link["rssi"] = quality_to_dbm(quality)
            link["notes"].append("rssi_estimated_from_percent")
        if rates:
            link["rx_rate"] = rates[0]
            link["tx_rate"] = rates[1] if len(rates) > 1 else rates[0]
        link["connected"] = bool(link["bssid"])
        _finish_link(link)
        if best is None or (link["connected"] and not best["connected"]):
            best = link
    if best is None:
        best = _new_link()
        best["source"] = "netsh"
    lowered = text.lower()
    if not best["connected"] and "location" in lowered and ("permission" in lowered or "access" in lowered):
        best["notes"].append("windows_location_permission")
    return best


def parse_netsh_networks(text):
    """Windows `netsh wlan show networks mode=bssid` -> neighbors."""
    neighbors = []
    ssid = None
    security = None
    current = None
    for indent, key, value in _netsh_pairs(text):
        low = key.lower()
        if re.fullmatch(r"ssid\s+\d+", low):
            ssid = value
            security = None
            current = None
        elif re.fullmatch(r"bssid\s+\d+", low):
            current = _neighbor(ssid=ssid, bssid=normalize_mac(value), security=security)
            neighbors.append(current)
        elif current is None:
            if low.startswith(_AUTH_KEYS):
                security = "Open" if value.lower() in ("open", "offen", "ouvrir", "abierta") else value
        else:
            if low == "rssi":
                current["rssi"] = _int(value)
            elif re.fullmatch(r"\d+\s*%", value) and current["rssi"] is None:
                current["rssi"] = quality_to_dbm(_int(value))
            elif "util" in low:
                percent = re.search(r"\((\d+)\s*%\)", value)
                current["utilization"] = int(percent.group(1)) if percent else None
            elif low.startswith(_CHANNEL_KEYS):
                current["channel"] = _int(value)
                if current["band"] is None:
                    current["band"] = band_of(current["channel"])
            elif low.startswith(_BAND_KEYS):
                current["band"] = band_of(None, hint=value)
            elif "802.11" in value and current["phy"] is None:
                current["phy"] = value
    return neighbors


# ------------------------------------------------------------------ Linux

def _split_terse(line):
    """Split one line of `nmcli -t` output on unescaped colons."""
    fields = []
    current = ""
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            current += line[index + 1]
            index += 2
            continue
        if char == ":":
            fields.append(current)
            current = ""
        else:
            current += char
        index += 1
    fields.append(current)
    return fields


def parse_nmcli(text):
    """`nmcli -t -f IN-USE,SSID,BSSID,CHAN,FREQ,RATE,SIGNAL,SECURITY dev wifi list` -> (link, neighbors)."""
    link = _new_link()
    link["source"] = "nmcli"
    neighbors = []
    for line in text.splitlines():
        fields = _split_terse(line)
        if len(fields) < 8:
            continue
        in_use, ssid, bssid, chan, freq, rate, signal, security = fields[:8]
        freq_mhz = _int(freq)
        channel = _int(chan)
        rssi = quality_to_dbm(_int(signal)) if _int(signal) is not None else None
        security = security.strip() or "Open"
        if security == "--":
            security = "Open"
        entry = _neighbor(ssid=ssid, bssid=normalize_mac(bssid), rssi=rssi, channel=channel,
                          band=band_of(channel, freq=freq_mhz), security=security)
        if in_use.strip() == "*":
            link.update({
                "connected": True, "ssid": entry["ssid"], "bssid": entry["bssid"], "rssi": rssi,
                "channel": channel, "band": entry["band"], "security": security, "tx_rate": _int(rate),
            })
            link["notes"].append("rssi_estimated_from_percent")
        else:
            neighbors.append(entry)
    return _finish_link(link), neighbors


def parse_iw_link(text):
    """`iw dev <iface> link` -> partial link dict (only the keys it knows)."""
    out = {}
    head = re.search(r"Connected to ((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", text)
    if not head:
        return out
    out["connected"] = True
    out["bssid"] = normalize_mac(head.group(1))
    for line in text.splitlines():
        key, sep, value = line.strip().partition(":")
        if not sep:
            continue
        key = key.lower()
        value = value.strip()
        if key == "ssid":
            out["ssid"] = value
        elif key == "freq":
            freq = int(float(value.split()[0]))
            out["channel"] = channel_from_freq(freq)
            out["band"] = band_of(None, freq=freq)
        elif key == "signal":
            out["rssi"] = _int(value)
        elif key in ("tx bitrate", "rx bitrate"):
            rate = re.match(r"([\d.]+)\s*MBit/s", value)
            if rate:
                out["tx_rate" if key.startswith("tx") else "rx_rate"] = int(float(rate.group(1)))
            width = re.search(r"(\d+)MHz", value)
            if width:
                out["width"] = int(width.group(1))
            if "EHT-" in value:
                out["phy"] = "802.11be"
            elif "HE-" in value:
                out["phy"] = "802.11ax"
            elif "VHT-" in value:
                out["phy"] = "802.11ac"
            elif "MCS" in value and "phy" not in out:
                out["phy"] = "802.11n"
    return out


def parse_proc_wireless(text):
    """/proc/net/wireless -> {iface: (rssi, noise)}."""
    out = {}
    for line in text.splitlines()[2:]:
        fields = line.replace(".", " ").split()
        if len(fields) < 5 or not fields[0].endswith(":"):
            continue
        try:
            level = int(fields[3])
            noise = int(fields[4])
        except ValueError:
            continue
        out[fields[0].rstrip(":")] = (level if level < 0 else None, noise if -120 < noise < 0 else None)
    return out


# ------------------------------------------------------------- collection

def _mark_mine(link, neighbors):
    """Flag heard networks that belong to the user's own Wi-Fi (other mesh nodes or bands)."""
    if not link.get("connected"):
        return
    for item in neighbors:
        if link.get("ssid") and item.get("ssid") == link["ssid"]:
            item["mine"] = True
        elif link.get("bssid") and item.get("bssid") and item["bssid"][:14] == link["bssid"][:14]:
            item["mine"] = True
        elif link.get("ssid") is None and item.get("ssid") is None and item.get("bssid") is None:
            # Names hidden by the OS: a very strong network on our own channel is almost surely ours.
            if item.get("channel") == link.get("channel") and item.get("band") == link.get("band") \
                    and (item.get("rssi") or -100) >= -55:
                item["mine"] = True


def collect(iface_hint=None):
    """Return {"link": ..., "neighbors": [...], "notes": [...]} for this computer."""
    link = _new_link()
    neighbors = []
    notes = []
    platform = osutil.PLATFORM
    if platform == "mac":
        rc, text = osutil.run(["system_profiler", "-json", "SPAirPortDataType"], timeout=40)
        if text.strip():
            link, neighbors = parse_system_profiler(text[text.find("{"):] if "{" in text else text)
        if os.path.exists(AIRPORT) and (not link["connected"] or not neighbors):
            info = parse_airport_info(osutil.run([AIRPORT, "-I"], timeout=10)[1])
            if info["connected"] and not link["connected"]:
                link = info
            if not neighbors:
                neighbors = parse_airport_scan(osutil.run([AIRPORT, "-s"], timeout=20)[1])
        if link["connected"] and link["ssid"] is None:
            notes.append("macOS hides network names from command-line tools unless Location Services is "
                         "allowed for the terminal app. Signal and channel data is still used.")
    elif platform == "win":
        link = parse_netsh_interfaces(osutil.run(["netsh", "wlan", "show", "interfaces"], timeout=15)[1])
        neighbors = parse_netsh_networks(osutil.run(["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=20)[1])
        if "windows_location_permission" in link["notes"]:
            notes.append("Windows blocked Wi-Fi details. Turn on Settings > Privacy & security > Location > "
                         "'Let desktop apps access your location', then run again.")
        if "rssi_estimated_from_percent" in link["notes"]:
            notes.append("Windows reports signal as a percentage; dBm values are estimates.")
    else:
        rc, text = osutil.run(["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,CHAN,FREQ,RATE,SIGNAL,SECURITY",
                               "device", "wifi", "list"], timeout=20)
        if text.strip():
            link, neighbors = parse_nmcli(text)
        iface = iface_hint
        if not iface:
            match = re.search(r"Interface\s+(\S+)", osutil.run(["iw", "dev"], timeout=8)[1])
            iface = match.group(1) if match else None
        if iface:
            extra = parse_iw_link(osutil.run(["iw", "dev", iface, "link"], timeout=8)[1])
            if extra:
                precise = "rssi" in extra
                for key, value in extra.items():
                    if value is not None:
                        link[key] = value
                link["iface"] = iface
                link["source"] = "iw"
                if precise and "rssi_estimated_from_percent" in link["notes"]:
                    link["notes"].remove("rssi_estimated_from_percent")
        levels = parse_proc_wireless(osutil.read_file("/proc/net/wireless"))
        if levels:
            name = iface if iface in levels else sorted(levels)[0]
            level, noise = levels[name]
            link["iface"] = link["iface"] or name
            if level is not None and (link["rssi"] is None or "rssi_estimated_from_percent" in link["notes"]):
                link["rssi"] = level
                link["connected"] = True
            if noise is not None:
                link["noise"] = noise
        _finish_link(link)
    _mark_mine(link, neighbors)
    if not link["connected"] and not neighbors:
        notes.append("No Wi-Fi adapter data on this computer (wired only, Wi-Fi off, or blocked). "
                     "Use the phone survey to map coverage.")
    link.pop("notes", None)
    return {"link": link, "neighbors": neighbors, "notes": notes}
