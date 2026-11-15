"""Connection stability over time: finds drops and tells local from provider faults."""
from __future__ import annotations

import json
import os
import time
import urllib.request

from . import netinfo
from .speed import ANCHORS, dns_query_ms


def take_sample(gateway, anchors=ANCHORS, dns_server=None):
    """One reading: router reachable? internet reachable? how fast?"""
    sample = {"t": round(time.time(), 1), "gw_ms": None, "gw_loss": None, "net_ms": None, "net_loss": None,
              "dns_ms": None}
    if gateway:
        stats = netinfo.ping(gateway, count=3, interval=0.2, timeout=1.0)
        sample["gw_ms"] = stats.get("median")
        sample["gw_loss"] = stats.get("loss_pct")
    best = None
    for anchor in anchors[:2]:
        stats = netinfo.ping(anchor, count=3, interval=0.2, timeout=1.0)
        if stats.get("median") is not None and (best is None or stats["loss_pct"] < best["loss_pct"]):
            best = stats
        if best and best["loss_pct"] == 0:
            break
    if best:
        sample["net_ms"] = best["median"]
        sample["net_loss"] = best["loss_pct"]
    else:
        sample["net_loss"] = 100.0
    if dns_server:
        elapsed = dns_query_ms(dns_server, "www.google.com", timeout=2.0)
        sample["dns_ms"] = round(elapsed, 1) if elapsed is not None else None
    return sample


def state_of(sample):
    """'ok', 'local' (router unreachable: Wi-Fi/router fault) or 'upstream' (provider/modem fault)."""
    net_down = sample.get("net_ms") is None
    gw_known = sample.get("gw_loss") is not None
    gw_down = gw_known and sample.get("gw_ms") is None
    if not net_down:
        return "ok"
    if gw_down:
        return "local"
    return "upstream"


def summarize(samples):
    """Turn raw samples into uptime, outages and latency percentiles."""
    summary = {
        "samples": len(samples), "start": None, "end": None, "duration_min": 0.0, "uptime_pct": None,
        "outages": [], "local_outages": 0, "upstream_outages": 0, "downtime_s": 0.0,
        "gw_ms": None, "net_ms": None, "net_p95_ms": None, "gw_p95_ms": None, "spikes": 0, "by_hour": [],
        "dns_failures": 0,
    }
    if not samples:
        return summary
    samples = sorted(samples, key=lambda s: s["t"])
    summary["start"], summary["end"] = samples[0]["t"], samples[-1]["t"]
    summary["duration_min"] = round((samples[-1]["t"] - samples[0]["t"]) / 60.0, 1)
    gaps = [b["t"] - a["t"] for a, b in zip(samples, samples[1:])]
    step = sorted(gaps)[len(gaps) // 2] if gaps else 5.0
    ok = 0
    current = None
    for sample in samples:
        state = state_of(sample)
        if state == "ok":
            ok += 1
            if current:
                current["end"] = sample["t"]
                current["seconds"] = round(current["end"] - current["start"], 1)
                summary["outages"].append(current)
                current = None
        else:
            if current is None:
                current = {"start": sample["t"], "end": None, "seconds": None, "scope": state}
            elif state == "local":
                current["scope"] = "local"
    if current:
        current["end"] = samples[-1]["t"] + step
        current["seconds"] = round(current["end"] - current["start"], 1)
        summary["outages"].append(current)
    summary["uptime_pct"] = round(100.0 * ok / len(samples), 2)
    summary["local_outages"] = sum(1 for o in summary["outages"] if o["scope"] == "local")
    summary["upstream_outages"] = sum(1 for o in summary["outages"] if o["scope"] == "upstream")
    summary["downtime_s"] = round(sum(o["seconds"] for o in summary["outages"]), 1)

    def percentile(values, fraction):
        values = sorted(values)
        return round(values[min(len(values) - 1, int(round(fraction * (len(values) - 1))))], 1) if values else None

    net = [s["net_ms"] for s in samples if s.get("net_ms") is not None]
    gw = [s["gw_ms"] for s in samples if s.get("gw_ms") is not None]
    summary["net_ms"] = percentile(net, 0.5)
    summary["net_p95_ms"] = percentile(net, 0.95)
    summary["gw_ms"] = percentile(gw, 0.5)
    summary["gw_p95_ms"] = percentile(gw, 0.95)
    if net:
        typical = summary["net_ms"]
        summary["spikes"] = sum(1 for value in net if value > max(100.0, typical * 3))
    summary["dns_failures"] = sum(1 for s in samples if "dns_ms" in s and s.get("dns_ms") is None
                                  and s.get("net_ms") is not None and s.get("dns_checked"))
    hours = {}
    for sample in samples:
        hour = time.localtime(sample["t"]).tm_hour
        bucket = hours.setdefault(hour, {"hour": hour, "n": 0, "down": 0, "net": []})
        bucket["n"] += 1
        if state_of(sample) != "ok":
            bucket["down"] += 1
        if sample.get("net_ms") is not None:
            bucket["net"].append(sample["net_ms"])
    for hour in sorted(hours):
        bucket = hours[hour]
        summary["by_hour"].append({
            "hour": hour, "samples": bucket["n"], "down": bucket["down"],
            "net_ms": percentile(bucket["net"], 0.5), "net_p95_ms": percentile(bucket["net"], 0.95),
        })
    return summary


def load_samples(path, limit=200000):
    samples = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    samples.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return samples[-limit:]


def post_webhook(url, payload):
    """Send an alert to a webhook (IFTTT Webhooks, Zapier or any JSON endpoint)."""
    body = dict(payload)
    body.setdefault("value1", payload.get("event"))
    body.setdefault("value2", payload.get("detail"))
    body.setdefault("value3", payload.get("when"))
    try:
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "wifiscout"})
        urllib.request.urlopen(request, timeout=8).read(256)
        return True
    except Exception:
        return False


def run(path, minutes=60.0, interval=10.0, webhook=None, progress=None, net=None):
    """Sample until the time is up or Ctrl-C. Appends JSON lines to `path`."""
    say = progress or (lambda message: None)
    net = net or netinfo.get_network()
    gateway = net.get("gateway")
    dns_servers = [s for s in net.get("dns") or [] if ":" not in s and not s.startswith("127.")]
    dns_server = dns_servers[0] if dns_servers else None
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    stop_at = time.time() + minutes * 60.0
    last_state = "ok"
    down_since = None
    pending_alert = None
    count = 0
    try:
        with open(path, "a", encoding="utf-8") as handle:
            while time.time() < stop_at:
                started = time.time()
                sample = take_sample(gateway, dns_server=dns_server)
                sample["dns_checked"] = bool(dns_server)
                handle.write(json.dumps(sample) + "\n")
                handle.flush()
                count += 1
                state = state_of(sample)
                stamp = time.strftime("%H:%M:%S", time.localtime(sample["t"]))
                if state == "ok":
                    say("%s  router %s ms   internet %s ms   %s" % (
                        stamp, _fmt(sample["gw_ms"]), _fmt(sample["net_ms"]),
                        "packet loss %d%%" % sample["net_loss"] if sample.get("net_loss") else "ok"))
                elif state == "local":
                    say("%s  DOWN: this computer cannot reach the router (Wi-Fi or router problem)" % stamp)
                else:
                    say("%s  DOWN: router is fine but the internet is not (modem or provider problem)" % stamp)
                if state != "ok" and last_state == "ok":
                    down_since = sample["t"]
                    pending_alert = {"event": "internet_down", "detail": state,
                                     "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sample["t"]))}
                elif state == "ok" and last_state != "ok":
                    seconds = int(sample["t"] - (down_since or sample["t"]))
                    if webhook:
                        # The 'down' alert cannot leave while offline, so both go out on recovery.
                        if pending_alert:
                            post_webhook(webhook, pending_alert)
                        post_webhook(webhook, {"event": "internet_restored", "detail": "%s outage, %d s" % (last_state, seconds),
                                               "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sample["t"]))})
                    pending_alert = None
                last_state = state
                wait = interval - (time.time() - started)
                if wait > 0:
                    time.sleep(wait)
    except KeyboardInterrupt:
        say("Stopped.")
    return count


def _fmt(value):
    return "-" if value is None else ("%.0f" % value if value >= 10 else "%.1f" % value)
