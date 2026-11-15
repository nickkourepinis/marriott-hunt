"""Sample data: a two-storey house with a three-node eero mesh and some typical faults.

Used by `wifiscout demo` to show what a report looks like without touching any network.
Every device, name and number here is invented.
"""
from __future__ import annotations

import math
import random
import time

from . import __version__, channels, devices, modem, monitor, wifi


def _device(ip, mac, vendor, hostname=None, gateway=False, self_=False, services=None, txt=None, ports=None,
            rtt=None, loss=0.0, names=None):
    record = {
        "ip": ip, "mac": mac, "vendor": vendor, "random_mac": bool(int(mac[:2], 16) & 2), "hostname": hostname,
        "name": None, "self": self_, "gateway": gateway, "ports": ports or [], "rtt_ms": rtt, "loss_pct": loss,
        "mdns": {"names": names or [], "services": services or [], "txt": txt or {}, "hostname": None}
        if (services or names) else None,
        "ssdp": None,
    }
    record.update(devices.classify(record))
    record["name"] = devices.display_name(record) or record["label"]
    return record


def build():
    """Returns (audit, survey, eero, monitor_samples) dictionaries."""
    now = time.time()
    rng = random.Random(7)
    device_list = [
        _device("192.168.4.1", "08:f0:1e:10:20:30", "eero inc.", gateway=True, ports=[53], rtt=3.1),
        _device("192.168.4.20", "08:f0:1e:10:21:40", "eero inc.", hostname="eero-hallway", ports=[53], rtt=6.4),
        _device("192.168.4.21", "08:f0:1e:10:22:50", "eero inc.", hostname="eero-office", ports=[53], rtt=19.5, loss=0),
        _device("192.168.4.23", "a4:83:e7:11:22:33", "Apple, Inc.", hostname="Study-MacBook", self_=True),
        _device("192.168.4.30", "f0:b3:ec:01:02:03", "Apple, Inc.", services=["_airplay._tcp", "_companion-link._tcp"],
                txt={"model": "AppleTV11,1"}, names=["Living Room"], rtt=4.0),
        _device("192.168.4.31", "d4:a3:3d:0a:0b:0c", "Apple, Inc.", services=["_airplay._tcp", "_raop._tcp"],
                txt={"model": "AudioAccessory6,1"}, names=["Kitchen HomePod"], rtt=5.2),
        _device("192.168.4.32", "64:16:66:aa:bb:01", "Nest Labs Inc.", hostname="Nest-Thermostat", rtt=28.0),
        _device("192.168.4.33", "9c:8e:cd:44:55:66", "Amcrest Technologies", hostname="garage-cam", ports=[554, 80],
                rtt=212.0, loss=50.0),
        _device("192.168.4.34", "54:2a:1b:de:ad:01", "Sonos, Inc.", services=["_sonos._tcp"], names=["Den Sonos"], rtt=7.5),
        _device("192.168.4.35", "24:0a:c4:12:34:56", "Espressif Inc.", hostname="patio-plug", rtt=96.0, loss=25.0),
        _device("192.168.4.36", "24:0a:c4:12:34:57", "Espressif Inc.", hostname="lamp-plug", rtt=11.0),
        _device("192.168.4.37", "3c:2a:f4:77:88:99", "Brother Industries, LTD.", services=["_ipp._tcp"],
                txt={"ty": "Brother HL-L2350DW"}, names=["Brother HL-L2350DW"], rtt=9.0),
        _device("192.168.4.38", "7c:d5:66:10:10:10", "Amazon Technologies Inc.", hostname="echo-bedroom", rtt=44.0),
        _device("192.168.4.39", "c8:3a:6b:20:20:20", "Roku, Inc", hostname="Roku-Bedroom", rtt=61.0, loss=25.0),
        _device("192.168.4.40", "f2:11:9a:33:44:55", None, rtt=14.0),
        _device("192.168.4.41", "de:ad:be:ef:00:42", None, ports=[62078], rtt=18.0),
        _device("192.168.4.42", "b0:be:83:5a:5b:5c", "Apple, Inc.", hostname="Kids-iPad", rtt=22.0),
        _device("192.168.4.43", "00:11:32:9a:9b:9c", "Synology Incorporated", hostname="DiskStation", ports=[5000, 445], rtt=1.2),
        _device("192.168.4.44", "98:b6:e9:01:01:01", "Nintendo Co.,Ltd", hostname="Nintendo-Switch", rtt=17.0),
        _device("192.168.4.45", "44:07:0b:02:02:02", "Google, Inc.", services=["_googlecast._tcp"],
                txt={"md": "Chromecast Ultra", "fn": "Basement TV"}, names=["Basement TV"], rtt=31.0),
    ]
    device_list.sort(key=lambda d: tuple(int(p) for p in d["ip"].split(".")))
    counts = {}
    for item in device_list:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    scan = {
        "network": None, "devices": device_list, "notes": [],
        "summary": {"total": len(device_list), "by_kind": counts, "infrastructure": [],
                    "private_addresses": sum(1 for d in device_list if d["random_mac"])},
    }
    network = {"hostname": "Study-MacBook", "local_ip": "192.168.4.23", "gateway": "192.168.4.1", "iface": "en0",
               "prefix": 22, "network": "192.168.4.0/22", "mac": "a4:83:e7:11:22:33", "dns": ["192.168.4.1"], "via": "wifi"}
    link = {
        "connected": True, "iface": "en0", "ssid": "Maple House", "bssid": "08:f0:1e:10:22:52", "rssi": -67,
        "noise": -91, "snr": 24, "channel": 44, "band": "5", "width": 80, "phy": "802.11ax",
        "generation": "Wi-Fi 6", "tx_rate": 432, "rx_rate": None, "security": "WPA2/WPA3 Personal",
        "source": "system_profiler",
    }
    names = ["NETGEAR58", "xfinitywifi", "Linksys01882", "TP-Link_4C1A", "ATT9xKp3", "MySpectrumWiFi70", "Casa Verde",
             "DIRECT-roku-431", "HP-Print-A2", "orbi_guest", "The Burrow", "FiOS-QW3LM"]
    neighbors = [
        {"ssid": "Maple House", "bssid": "08:f0:1e:10:20:31", "rssi": -58, "channel": 6, "band": "2.4", "width": 20},
        {"ssid": "Maple House", "bssid": "08:f0:1e:10:21:42", "rssi": -49, "channel": 44, "band": "5", "width": 80},
    ]
    plan = [(6, -52), (6, -61), (6, -70), (1, -66), (1, -78), (11, -81), (3, -74), (9, -83), (6, -79),
            (44, -72), (44, -80), (149, -77)]
    for index, (channel, rssi) in enumerate(plan):
        neighbors.append({"ssid": names[index], "bssid": "10:da:43:%02x:%02x:%02x" % (index, index * 3, index * 7),
                          "rssi": rssi, "channel": channel, "band": wifi.band_of(channel), "width": 20 if channel < 15 else 80})
    for item in neighbors:
        item.update({"noise": None, "phy": None, "security": "WPA2 Personal", "utilization": None,
                     "mine": item["ssid"] == "Maple House"})
    link24 = dict(link)
    wifi_section = {"link": link, "neighbors": neighbors, "notes": [], "channels": channels.analyze(link, neighbors)}
    # show the 2.4 GHz picture as the gateway's own radio sees it
    wifi_section["channels"]["bands"]["2.4"] = channels.analyze(
        dict(link24, channel=6, band="2.4", width=20), neighbors)["bands"]["2.4"]

    def stats(median, jitter, loss, count=20):
        return {"sent": count, "received": int(count * (1 - loss / 100.0)), "loss_pct": loss, "min": round(median * 0.8, 1),
                "avg": round(median * 1.1, 1), "median": median, "max": round(median * 2.4, 1), "jitter": jitter,
                "p95": round(median * 1.9, 1), "method": "icmp"}

    latency = {"gateway": stats(7.8, 3.9, 0.0), "internet": stats(21.5, 6.2, 0.0), "internet_target": "1.1.1.1",
               "anchors": {"1.1.1.1": stats(21.5, 6.2, 0.0), "8.8.8.8": stats(24.0, 5.1, 0.0)}, "online": True}
    dns = {
        "system": [{"label": "Your DNS", "server": "192.168.4.1", "failures": 0, "samples": 6, "avg_ms": 74.0, "max_ms": 131.0, "local_cache": False}],
        "public": [{"label": "Cloudflare", "server": "1.1.1.1", "failures": 0, "samples": 6, "avg_ms": 22.4, "max_ms": 31.0, "local_cache": False},
                   {"label": "Google", "server": "8.8.8.8", "failures": 0, "samples": 6, "avg_ms": 25.9, "max_ms": 38.0, "local_cache": False},
                   {"label": "Quad9", "server": "9.9.9.9", "failures": 0, "samples": 6, "avg_ms": 29.3, "max_ms": 40.0, "local_cache": False}],
        "best_public": {"label": "Cloudflare", "server": "1.1.1.1", "failures": 0, "samples": 6, "avg_ms": 22.4, "max_ms": 31.0},
        "system_avg_ms": 74.0,
    }
    speed = {"method": "http", "server": "Cloudflare", "down_mbps": 318.4, "up_mbps": 21.7, "bytes_down": 318000000,
             "bytes_up": 21000000, "idle_ms": 21.5, "loaded_down_ms": 58.0, "loaded_up_ms": 164.0, "bloat_ms": 142.5,
             "bloat_grade": "C", "rpm": None, "errors": [], "isp": "Example Cable", "asn": 64500, "colo": "EWR",
             "country": "US", "public_ip_masked": "203.x.x.17", "public_ip_cgnat": False, "via": "wifi"}
    router = {"gateway": "192.168.4.1", "brand": "eero", "model": None, "label": "eero gateway (main router)", "upnp": True,
              "wan_ip_masked": "203.x.x.17", "wan_ip_private": False, "uptime_s": 912000, "link_down_mbps": None,
              "link_up_mbps": None, "hops": ["192.168.4.1", "96.x.x.1", "68.x.x.33"], "double_nat": False,
              "cgnat": False, "evidence": [], "extra_routers": []}
    down_channels = [{"channel": str(i + 1), "modulation": "QAM256", "frequency": 483.0 + 6 * i,
                      "power": round(-3.5 + rng.uniform(-1.4, 1.6), 1), "snr": round(38.5 + rng.uniform(-1.5, 1.2), 1),
                      "corrected": rng.randint(0, 400), "uncorrected": rng.randint(0, 30)} for i in range(24)]
    down_channels.append({"channel": "159", "modulation": "Other", "frequency": 722.0, "power": -4.1, "snr": 37.9,
                          "corrected": 120344, "uncorrected": 0})
    up_channels = [{"channel": str(i + 1), "modulation": "SC-QAM Upstream", "frequency": 16.4 + 6.4 * i,
                    "power": round(49.5 + rng.uniform(-0.4, 1.0), 1)} for i in range(4)]
    modem_section = {"status": "ok", "address": "192.168.100.1", "url": "http://192.168.100.1/cmconnectionstatus.html",
                     "kind": "cable", "model": "ARRIS SB8200", "downstream": down_channels, "upstream": up_channels,
                     "summary": modem.summarize(down_channels, up_channels), "cellular": None}
    audit = {
        "meta": {"version": __version__, "generated": now, "platform": "mac", "os": "Sample", "quick": False, "demo": True},
        "errors": [], "network": network, "scan": scan, "wifi": wifi_section, "latency": latency, "dns": dns,
        "speed": speed, "router": router, "modem": modem_section,
    }

    def spot(sid, name, floor, x, y, rssi, down, up, rtt, jitter, loss, channel, bssid, minutes):
        return {"id": sid, "name": name, "floor": floor, "x": x, "y": y, "ts": now - 3600 + minutes * 60, "rssi": rssi,
                "noise": -92, "down_mbps": down, "up_mbps": up, "rtt_ms": rtt, "jitter_ms": jitter, "loss_pct": loss,
                "channel": channel, "band": wifi.band_of(channel), "bssid": bssid, "tx_rate": None, "source": "phone",
                "radio_source": "shortcut"}

    gw, hall, office = "08:f0:1e:10:20:32", "08:f0:1e:10:21:42", "08:f0:1e:10:22:52"
    spots = [
        spot("s1", "Living room", "Main floor", 2.0, 3.0, -41, 312, 284, 4, 1.1, 0, 44, gw, 0),
        spot("s2", "Kitchen", "Main floor", 6.5, 2.5, -55, 248, 201, 5, 1.6, 0, 44, gw, 2),
        spot("s3", "Dining room", "Main floor", 6.0, 6.5, -60, 171, 140, 6, 2.2, 0, 44, gw, 4),
        spot("s4", "Front hall", "Main floor", 2.5, 8.0, -52, 222, 180, 5, 1.4, 0, 44, hall, 6),
        spot("s5", "Den", "Main floor", 10.5, 7.0, -71, 38, 22, 19, 9.5, 0, 44, gw, 8),
        spot("s6", "Laundry", "Main floor", 11.5, 2.5, -76, 12, 5, 42, 18.0, 2, 6, gw, 10),
        spot("s7", "Garage", "Main floor", 15.0, 4.5, -84, 2.4, 0.8, 140, 62.0, 8, 6, gw, 12),
        spot("s8", "Patio", "Main floor", 9.0, 11.5, -79, 6.1, 2.2, 88, 35.0, 4, 6, gw, 14),
        spot("u1", "Upstairs landing", "Upstairs", 2.5, 7.5, -50, 186, 150, 7, 2.0, 0, 44, hall, 20),
        spot("u2", "Main bedroom", "Upstairs", 2.5, 3.0, -63, 96, 71, 9, 3.5, 0, 44, hall, 22),
        spot("u3", "Office", "Upstairs", 7.5, 6.5, -54, 41, 30, 24, 11.0, 0, 44, office, 24),
        spot("u4", "Kids room", "Upstairs", 8.0, 2.5, -66, 33, 20, 27, 12.0, 1, 44, office, 26),
        spot("u5", "Guest room", "Upstairs", 12.0, 5.0, -77, 8, 3, 55, 24.0, 3, 44, office, 28),
        spot("u6", "Bathroom", "Upstairs", 5.0, 9.5, -69, 52, 33, 15, 6.0, 0, 44, hall, 30),
    ]
    survey = {"profile": {"home": "house", "sqft": 2400, "plan_down": 500, "plan_up": 20},
              "floors": ["Main floor", "Upstairs"], "spots": spots, "plans": {}, "stride_m": 0.7, "version": 1}
    eero = {
        "network": {"name": "Maple House", "status": "connected", "isp_up": True, "bridge": False, "wan_type": "dhcp",
                    "speed_down": 331.0, "speed_up": 22.0, "speed_date": None, "band_steering": True, "sqm": False,
                    "wpa3": True, "upnp": True, "thread": True, "update_available": True},
        "nodes": [
            {"name": "Living room", "model": "eero Pro 6E", "gateway": True, "wired": True, "status": "green",
             "mesh_bars": 5, "clients": 11, "ip": "192.168.4.1", "firmware": "7.4.1", "update_available": True,
             "heartbeat_ok": True, "bands": []},
            {"name": "Hallway", "model": "eero Pro 6E", "gateway": False, "wired": False, "status": "green",
             "mesh_bars": 4, "clients": 5, "ip": "192.168.4.20", "firmware": "7.4.1", "update_available": True,
             "heartbeat_ok": True, "bands": []},
            {"name": "Office", "model": "eero 6+", "gateway": False, "wired": False, "status": "green",
             "mesh_bars": 2, "clients": 4, "ip": "192.168.4.21", "firmware": "7.4.1", "update_available": True,
             "heartbeat_ok": True, "bands": []},
        ],
        "clients": [
            {"name": "Garage camera", "mac": "9c:8e:cd:44:55:66", "ip": "192.168.4.33", "connected": True, "wireless": True,
             "band": "2.4", "rssi": -83, "score_bars": 1, "node": "Living room", "channel": 6, "rx_mbps": 6.5, "type": "camera", "manufacturer": "Amcrest"},
            {"name": "Patio plug", "mac": "24:0a:c4:12:34:56", "ip": "192.168.4.35", "connected": True, "wireless": True,
             "band": "2.4", "rssi": -78, "score_bars": 2, "node": "Living room", "channel": 6, "rx_mbps": 13.0, "type": "plug", "manufacturer": "Espressif"},
            {"name": "Roku Bedroom", "mac": "c8:3a:6b:20:20:20", "ip": "192.168.4.39", "connected": True, "wireless": True,
             "band": "5", "rssi": -74, "score_bars": 2, "node": "Office", "channel": 44, "rx_mbps": 58.0, "type": "media", "manufacturer": "Roku"},
            {"name": "Echo Bedroom", "mac": "7c:d5:66:10:10:10", "ip": "192.168.4.38", "connected": True, "wireless": True,
             "band": "5", "rssi": -68, "score_bars": 3, "node": "Office", "channel": 44, "rx_mbps": 86.0, "type": "speaker", "manufacturer": "Amazon"},
            {"name": "Nest Thermostat", "mac": "64:16:66:aa:bb:01", "ip": "192.168.4.32", "connected": True, "wireless": True,
             "band": "2.4", "rssi": -61, "score_bars": 4, "node": "Hallway", "channel": 6, "rx_mbps": 52.0, "type": "thermostat", "manufacturer": "Nest"},
            {"name": "Living Room Apple TV", "mac": "f0:b3:ec:01:02:03", "ip": "192.168.4.30", "connected": True, "wireless": True,
             "band": "5", "rssi": -44, "score_bars": 5, "node": "Living room", "channel": 44, "rx_mbps": 866.0, "type": "media", "manufacturer": "Apple"},
            {"name": "Kitchen HomePod", "mac": "d4:a3:3d:0a:0b:0c", "ip": "192.168.4.31", "connected": True, "wireless": True,
             "band": "5", "rssi": -57, "score_bars": 4, "node": "Living room", "channel": 44, "rx_mbps": 390.0, "type": "speaker", "manufacturer": "Apple"},
        ],
    }
    samples = []
    start = now - 6 * 3600
    for index in range(6 * 360):
        t = start + index * 10
        hour_wave = 6 * math.sin(index / 180.0)
        net = 21 + hour_wave + rng.uniform(-2, 4)
        if rng.random() < 0.01:
            net += rng.uniform(60, 180)
        sample = {"t": round(t, 1), "gw_ms": round(6 + rng.uniform(-2, 5), 1), "gw_loss": 0.0,
                  "net_ms": round(net, 1), "net_loss": 0.0, "dns_ms": 70.0, "dns_checked": True}
        if 700 <= index < 709 or 1500 <= index < 1504:
            sample.update({"net_ms": None, "net_loss": 100.0})
        samples.append(sample)
    return audit, survey, eero, samples


def write(datadir):
    """Write the sample files into a data folder."""
    import json

    audit, survey, eero, samples = build()
    datadir.save_json("audit.json", audit)
    datadir.save_json("survey.json", survey)
    datadir.save_json("eero.json", eero)
    with open(datadir.path("monitor.jsonl"), "w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample) + "\n")
    return monitor.summarize(samples)
