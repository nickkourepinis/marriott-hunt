"""Speed and responsiveness: latency split, DNS timing, throughput, bufferbloat."""
from __future__ import annotations

import http.client
import json
import os
import random
import socket
import ssl
import statistics
import threading
import time

from . import dnsmsg, netinfo, osutil

USER_AGENT = "wifiscout/1.0 (home network diagnostics)"
ANCHORS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")
PUBLIC_DNS = (("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8"), ("Quad9", "9.9.9.9"))
DNS_NAMES = ("www.google.com", "www.apple.com", "www.amazon.com", "www.netflix.com",
             "www.youtube.com", "www.microsoft.com")


class Endpoint(object):
    """An HTTP server that can send and swallow bytes on request."""

    def __init__(self, name, scheme, host, port, down_path, up_path, meta_path=None):
        self.name = name
        self.scheme = scheme
        self.host = host
        self.port = port
        self.down_path = down_path  # must contain %d for the byte count
        self.up_path = up_path
        self.meta_path = meta_path

    def connect(self, timeout=10.0):
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=_tls_context())
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)


CLOUDFLARE = Endpoint("Cloudflare", "https", "speed.cloudflare.com", 443, "/__down?bytes=%d", "/__up", "/meta")

_context = None


def _tls_context():
    """Verifying TLS context. Also trusts the macOS system bundle, which python.org builds ignore."""
    global _context
    if _context is None:
        context = ssl.create_default_context()
        for bundle in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
            if osutil.PLATFORM == "mac" and os.path.exists(bundle):
                try:
                    context.load_verify_locations(bundle)
                    break
                except (OSError, ssl.SSLError):
                    pass
        _context = context
    return _context


# ---------------------------------------------------------------- latency

def latency_profile(gateway, count=20, anchors=ANCHORS):
    """Ping the router and the internet at the same time.

    A slow or lossy router hop means the problem is inside the home. A clean
    router hop with a bad internet hop points at the modem or the provider.
    """
    targets = ([gateway] if gateway else []) + list(anchors)
    results = {}

    def work(target):
        results[target] = netinfo.ping(target, count=count, interval=0.2, timeout=1.0)

    threads = [threading.Thread(target=work, args=(target,)) for target in targets]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(timeout=count * 1.3 + 10)
    anchor_stats = {a: results.get(a) for a in anchors if results.get(a)}
    reachable = {a: s for a, s in anchor_stats.items() if s.get("median") is not None}
    best = min(reachable, key=lambda a: reachable[a]["median"]) if reachable else None
    return {
        "gateway": results.get(gateway) if gateway else None,
        "anchors": anchor_stats,
        "internet": reachable.get(best) if best else None,
        "internet_target": best,
        "online": bool(reachable),
    }


# -------------------------------------------------------------------- DNS

def dns_query_ms(server, name, timeout=2.0):
    """Milliseconds for one A-record lookup against a resolver, or None on failure."""
    qid = random.randint(1, 65535)
    packet = dnsmsg.build_query([(name, dnsmsg.TYPE_A)], qid=qid)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        start = time.perf_counter()
        sock.sendto(packet, (server, 53))
        while True:
            data, _ = sock.recvfrom(2048)
            if len(data) >= 2 and (data[0] << 8 | data[1]) == qid:
                return (time.perf_counter() - start) * 1000.0
    except OSError:
        return None
    finally:
        sock.close()


def _dns_server_stats(label, server, names):
    times = []
    failures = 0
    for name in names:
        elapsed = dns_query_ms(server, name)
        if elapsed is None:
            failures += 1
        else:
            times.append(elapsed)
    return {
        "label": label, "server": server, "failures": failures, "samples": len(names),
        "avg_ms": round(sum(times) / len(times), 1) if times else None,
        "max_ms": round(max(times), 1) if times else None,
        "local_cache": server.startswith("127."),
    }


def dns_profile(system_servers, names=DNS_NAMES):
    """Compare the resolvers this computer uses with well-known public ones."""
    jobs = [("Your DNS", server) for server in (system_servers or [])[:3] if ":" not in server]
    seen = {server for _, server in jobs}
    jobs += [(label, server) for label, server in PUBLIC_DNS if server not in seen]
    results = {}

    def work(job):
        results[job] = _dns_server_stats(job[0], job[1], names)

    threads = [threading.Thread(target=work, args=(job,)) for job in jobs]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(timeout=len(names) * 2.2 + 2)
    system = [results[j] for j in jobs if j[0] == "Your DNS" and j in results]
    for label, server in PUBLIC_DNS:
        if server in seen:
            for item in system:
                if item["server"] == server:
                    item["label"] = "Your DNS (%s)" % label
    public = [results[j] for j in jobs if j[0] != "Your DNS" and j in results]
    usable_public = [p for p in public if p["avg_ms"] is not None and p["failures"] <= 1]
    best_public = min(usable_public, key=lambda p: p["avg_ms"]) if usable_public else None
    usable_system = [s for s in system if s["avg_ms"] is not None]
    return {
        "system": system,
        "public": public,
        "best_public": best_public,
        "system_avg_ms": round(min(s["avg_ms"] for s in usable_system), 1) if usable_system else None,
    }


# ------------------------------------------------------------- throughput

class _Counter(object):
    def __init__(self):
        self.total = 0
        self.lock = threading.Lock()
        self.samples = []
        self.errors = []

    def add(self, amount):
        with self.lock:
            self.total += amount

    def snapshot(self):
        with self.lock:
            self.samples.append((time.perf_counter(), self.total))


def _download_worker(endpoint, stop_at, counter, chunk_bytes):
    while time.perf_counter() < stop_at:
        conn = None
        try:
            conn = endpoint.connect()
            conn.request("GET", endpoint.down_path % chunk_bytes,
                         headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status != 200:
                counter.errors.append("HTTP %d from %s" % (response.status, endpoint.host))
                return
            got_any = False
            while time.perf_counter() < stop_at:
                data = response.read1(65536)
                if not data:
                    break
                got_any = True
                counter.add(len(data))
            if not got_any:
                counter.errors.append("empty response from %s" % endpoint.host)
                return
        except (OSError, http.client.HTTPException) as exc:
            counter.errors.append("%s: %s" % (type(exc).__name__, exc))
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass


def _upload_worker(endpoint, stop_at, counter, request_bytes, block):
    while time.perf_counter() < stop_at:
        conn = None
        try:
            conn = endpoint.connect()
            conn.putrequest("POST", endpoint.up_path)
            conn.putheader("User-Agent", USER_AGENT)
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(request_bytes))
            conn.endheaders()
            sent = 0
            while sent < request_bytes and time.perf_counter() < stop_at:
                piece = block[:min(len(block), request_bytes - sent)]
                conn.send(piece)
                sent += len(piece)
                counter.add(len(piece))
            if sent < request_bytes:
                return  # time is up: drop the connection mid-request
            response = conn.getresponse()
            response.read()
            if response.status >= 400:
                counter.errors.append("HTTP %d from %s" % (response.status, endpoint.host))
                return
        except (OSError, http.client.HTTPException) as exc:
            counter.errors.append("%s: %s" % (type(exc).__name__, exc))
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass


def _measure(direction, endpoint, duration, streams, warmup):
    """Run one direction. Returns (mbps or None, bytes moved, error or None)."""
    counter = _Counter()
    start = time.perf_counter()
    stop_at = start + duration
    threads = []
    block = os.urandom(65536)
    for _ in range(streams):
        if direction == "down":
            args = (endpoint, stop_at, counter, 25 * 1000 * 1000)
            thread = threading.Thread(target=_download_worker, args=args)
        else:
            args = (endpoint, stop_at, counter, 25 * 1000 * 1000, block)
            thread = threading.Thread(target=_upload_worker, args=args)
        thread.daemon = True
        thread.start()
        threads.append(thread)
    counter.snapshot()
    while time.perf_counter() < stop_at:
        time.sleep(0.1)
        counter.snapshot()
        if not any(thread.is_alive() for thread in threads):
            break
    counter.snapshot()
    for thread in threads:
        thread.join(timeout=2.0)
    samples = counter.samples
    total = samples[-1][1]
    if total <= 0:
        return None, 0, (counter.errors[0] if counter.errors else "no data moved")
    elapsed = samples[-1][0] - start
    cut = start + min(warmup, elapsed / 3.0)
    first = next((s for s in samples if s[0] >= cut), samples[0])
    last = samples[-1]
    span = last[0] - first[0]
    if span <= 0.2 or last[1] <= first[1]:
        first, span = (start, 0), last[0] - start
    mbps = (last[1] - first[1]) * 8.0 / span / 1e6 if span > 0 else None
    error = counter.errors[0] if counter.errors and len(counter.errors) >= streams else None
    return (round(mbps, 2) if mbps is not None else None), total, error


def _loaded_latency(target, duration, out, key):
    count = max(3, int(duration / 0.25))
    stats = netinfo.ping(target, count=count, interval=0.25, timeout=1.0)
    out[key] = stats


def bufferbloat_grade(extra_ms):
    """Letter grade for latency added while the line is busy."""
    if extra_ms is None:
        return None
    if extra_ms < 5:
        return "A+"
    if extra_ms < 30:
        return "A"
    if extra_ms < 60:
        return "B"
    if extra_ms < 200:
        return "C"
    if extra_ms < 400:
        return "D"
    return "F"


def mask_ip(ip):
    """Hide the middle of a public address so reports can be shared."""
    if not ip:
        return None
    parts = ip.split(".")
    if len(parts) == 4:
        return "%s.x.x.%s" % (parts[0], parts[3])
    return ip.split(":")[0] + ":…"


def parse_meta(text):
    """Cloudflare /meta JSON or /cdn-cgi/trace text -> {isp, asn, colo, country, ip}."""
    out = {"isp": None, "asn": None, "colo": None, "country": None, "ip": None}
    text = (text or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return out
        out["isp"] = data.get("asOrganization")
        out["asn"] = data.get("asn")
        colo = data.get("colo")
        out["colo"] = colo.get("iata") if isinstance(colo, dict) else colo
        out["country"] = data.get("country")
        out["ip"] = data.get("clientIp")
        return out
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key == "ip":
            out["ip"] = value.strip()
        elif key == "colo":
            out["colo"] = value.strip()
        elif key == "loc":
            out["country"] = value.strip()
    return out


def fetch_meta(endpoint=CLOUDFLARE):
    for path in (endpoint.meta_path, "/cdn-cgi/trace"):
        if not path:
            continue
        conn = None
        try:
            conn = endpoint.connect(timeout=6.0)
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Referer": "https://speed.cloudflare.com/"})
            response = conn.getresponse()
            body = response.read(65536).decode("utf-8", errors="replace")
            if response.status == 200:
                meta = parse_meta(body)
                if meta["ip"] or meta["isp"]:
                    return meta
        except (OSError, http.client.HTTPException):
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
    return parse_meta("")


def http_speedtest(endpoint=CLOUDFLARE, duration=8.0, streams=4, anchor="1.1.1.1", idle_ms=None, progress=None):
    """Multi-stream download then upload, with latency measured while each runs."""
    say = progress or (lambda message: None)
    result = {
        "method": "http", "server": endpoint.name, "down_mbps": None, "up_mbps": None,
        "bytes_down": 0, "bytes_up": 0, "idle_ms": idle_ms, "loaded_down_ms": None, "loaded_up_ms": None,
        "bloat_ms": None, "bloat_grade": None, "rpm": None, "errors": [],
    }
    warmup = min(1.5, duration / 4.0)
    for direction in ("down", "up"):
        say("Measuring %s speed" % ("download" if direction == "down" else "upload"))
        latency = {}
        pinger = None
        if anchor:
            pinger = threading.Thread(target=_loaded_latency, args=(anchor, duration, latency, direction))
            pinger.daemon = True
            pinger.start()
        mbps, moved, error = _measure(direction, endpoint, duration, streams, warmup)
        if pinger is not None:
            pinger.join(timeout=duration + 6)
        result["%s_mbps" % direction] = mbps
        result["bytes_%s" % direction] = moved
        if error:
            result["errors"].append("%s: %s" % (direction, error))
        stats = latency.get(direction)
        if stats and stats.get("median") is not None and mbps:
            result["loaded_%s_ms" % direction] = stats["median"]
    loaded = [v for v in (result["loaded_down_ms"], result["loaded_up_ms"]) if v is not None]
    if loaded and idle_ms is not None:
        result["bloat_ms"] = round(max(0.0, max(loaded) - idle_ms), 1)
        result["bloat_grade"] = bufferbloat_grade(result["bloat_ms"])
    return result


def parse_network_quality(text):
    """macOS `networkQuality -c` JSON -> speed result, or None if unusable."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(text[start:])
    except ValueError:
        return None
    down = data.get("dl_throughput")
    up = data.get("ul_throughput")
    if not down and not up:
        return None
    rpm_values = [data.get(k) for k in ("responsiveness", "dl_responsiveness", "ul_responsiveness")]
    rpm_values = [v for v in rpm_values if isinstance(v, (int, float)) and v > 0]
    rpm = min(rpm_values) if rpm_values else None
    base = data.get("base_rtt")
    result = {
        "method": "networkQuality", "server": "Apple", "down_mbps": round(down / 1e6, 2) if down else None,
        "up_mbps": round(up / 1e6, 2) if up else None, "bytes_down": 0, "bytes_up": 0,
        "idle_ms": round(base, 1) if isinstance(base, (int, float)) else None,
        "loaded_down_ms": None, "loaded_up_ms": None, "bloat_ms": None, "bloat_grade": None,
        "rpm": int(rpm) if rpm else None, "errors": [],
    }
    if rpm:
        loaded = 60000.0 / rpm
        result["loaded_down_ms"] = round(loaded, 1)
        if result["idle_ms"] is not None:
            result["bloat_ms"] = round(max(0.0, loaded - result["idle_ms"]), 1)
            result["bloat_grade"] = bufferbloat_grade(result["bloat_ms"])
    return result


def run_speedtest(duration=8.0, idle_ms=None, progress=None, endpoint=CLOUDFLARE, prefer_native=True):
    """Measure internet speed with the best tool available on this computer."""
    say = progress or (lambda message: None)
    result = None
    if prefer_native and osutil.PLATFORM == "mac" and osutil.which("networkQuality"):
        say("Measuring internet speed (Apple networkQuality, about 20 seconds)")
        result = parse_network_quality(osutil.run(["networkQuality", "-c"], timeout=90)[1])
    if result is None:
        result = http_speedtest(endpoint=endpoint, duration=duration, idle_ms=idle_ms, progress=say)
    meta = fetch_meta(endpoint) if endpoint is CLOUDFLARE else parse_meta("")
    result["isp"] = meta["isp"]
    result["asn"] = meta["asn"]
    result["colo"] = meta["colo"]
    result["country"] = meta["country"]
    result["public_ip_masked"] = mask_ip(meta["ip"])
    result["public_ip_cgnat"] = netinfo.is_cgnat_ip(meta["ip"]) if meta["ip"] else None
    result["_public_ip"] = meta["ip"]
    return result


def median_or_none(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None
