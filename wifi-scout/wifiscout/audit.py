"""Run every collector and assemble the data bundle the analysis reads."""
from __future__ import annotations

import platform
import time

from . import __version__, analyze, channels, modem, monitor, netinfo, osutil, router, scan, speed, wifi


def run_audit(say=None, quick=False, probe_ports=True, ping_devices=True, speed_seconds=8.0, do_speed=True,
              do_modem=True):
    """Measure everything this computer can see. Returns the audit dict (also saved as audit.json)."""
    say = say or (lambda message: None)
    audit = {
        "meta": {"version": __version__, "generated": time.time(), "platform": osutil.PLATFORM,
                 "os": "%s %s" % (platform.system(), platform.release()), "quick": bool(quick)},
        "errors": [],
    }

    def stage(name, title, function):
        say(title)
        try:
            audit[name] = function()
        except Exception as exc:  # one broken collector must not sink the audit
            audit[name] = None
            audit["errors"].append("%s: %s: %s" % (name, type(exc).__name__, exc))
            say("  (skipped: %s)" % exc)

    stage("network", "Reading this computer's network settings", netinfo.get_network)
    net = audit.get("network") or {}
    stage("scan", "Scanning the network for devices", lambda: scan.run_scan(
        progress=lambda message: say("  " + message), probe_ports=probe_ports and not quick,
        ping_devices=ping_devices and not quick, net=net))

    def collect_wifi():
        data = wifi.collect(net.get("iface") if net.get("via") == "wifi" else None)
        data["channels"] = channels.analyze(data["link"], data["neighbors"])
        return data

    stage("wifi", "Reading Wi-Fi signal and nearby networks", collect_wifi)
    stage("latency", "Timing the router and the internet", lambda: speed.latency_profile(
        net.get("gateway"), count=10 if quick else 20))
    stage("dns", "Timing DNS lookups", lambda: speed.dns_profile(net.get("dns")))
    online = bool((audit.get("latency") or {}).get("online"))
    public_ip = None
    if do_speed and not quick and online:
        idle = ((audit.get("latency") or {}).get("anchors") or {}).get("1.1.1.1") or {}
        stage("speed", "Measuring internet speed (uses data: skip with --no-speed)", lambda: speed.run_speedtest(
            duration=speed_seconds, idle_ms=idle.get("median"), progress=lambda message: say("  " + message)))
        if audit.get("speed"):
            public_ip = audit["speed"].pop("_public_ip", None)
            audit["speed"]["via"] = net.get("via")
    else:
        audit["speed"] = None
    stage("router", "Inspecting the router and the path to the internet", lambda: router.inspect(
        net, audit.get("scan"), public_ip=public_ip, trace=not quick))
    if do_modem:
        stage("modem", "Looking for the modem's signal page", lambda: modem.probe(net.get("gateway")))
    else:
        audit["modem"] = None
    return audit


def build_bundle(datadir, survey_data=None, collector_via=None):
    """Merge the saved audit, survey, eero pull and monitor log into one bundle."""
    audit = datadir.load_json("audit.json", {}) or {}
    survey = survey_data if survey_data is not None else (datadir.load_json("survey.json", {}) or {})
    eero = datadir.load_json("eero.json", None)
    samples = monitor.load_samples(datadir.path("monitor.jsonl"))
    network = audit.get("network") or {}
    bundle = {
        "meta": audit.get("meta") or {"version": __version__, "generated": None},
        "profile": survey.get("profile") or {},
        "network": network,
        "scan": audit.get("scan"),
        "wifi": audit.get("wifi"),
        "latency": audit.get("latency"),
        "dns": audit.get("dns"),
        "speed": audit.get("speed"),
        "router": audit.get("router"),
        "modem": audit.get("modem"),
        "eero": eero,
        "monitor": monitor.summarize(samples) if samples else None,
        "monitor_series": thin_series(samples),
        "survey": {
            "floors": survey.get("floors") or ["Main floor"],
            "spots": survey.get("spots") or [],
            "plans": survey.get("plans") or {},
            "collector_via": collector_via or network.get("via") or "unknown",
        },
        "errors": audit.get("errors") or [],
    }
    bundle["analysis"] = analyze.analyze(bundle)
    return bundle


def thin_series(samples, limit=400):
    """Down-sample the monitor log for charting: (time, internet ms or None)."""
    if not samples:
        return []
    step = max(1, len(samples) // limit)
    series = []
    for index in range(0, len(samples), step):
        window = samples[index:index + step]
        values = [s["net_ms"] for s in window if s.get("net_ms") is not None]
        down = any(s.get("net_ms") is None for s in window)
        series.append({"t": window[0]["t"], "ms": round(max(values), 1) if values else None, "down": down})
    return series
