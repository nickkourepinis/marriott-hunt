"""Inventory of everything on the home network."""
from __future__ import annotations

import ipaddress
import queue
import socket
import threading
import time

from . import devices as devices_mod
from . import discovery, netinfo, oui

PROBE_PORTS = (22, 53, 80, 443, 445, 554, 631, 1883, 3389, 5000, 5900, 7000, 8008, 8009, 8080, 8443,
               9100, 9999, 32400, 62078)


def parallel(function, items, workers=32, timeout=10.0):
    """Run function(item) for each item on daemon threads.

    Returns {item: result} for the calls that finished in time. Threads that
    are still stuck (slow name lookups) are abandoned, never joined.
    """
    todo = queue.Queue()
    done = queue.Queue()
    for item in items:
        todo.put(item)

    def worker():
        while True:
            try:
                item = todo.get_nowait()
            except queue.Empty:
                return
            try:
                done.put((item, function(item)))
            except Exception:
                done.put((item, None))

    count = min(workers, max(1, todo.qsize()))
    for _ in range(count):
        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
    results = {}
    deadline = time.time() + timeout
    expected = len(items)
    while len(results) < expected:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            item, value = done.get(timeout=min(remaining, 0.25))
        except queue.Empty:
            continue
        results[item] = value
    return results


def _reverse_name(ip):
    try:
        name = socket.gethostbyaddr(ip)[0]
    except (OSError, UnicodeError):
        return None
    return None if name == ip else name


def _open_ports(ip, ports=PROBE_PORTS, timeout=0.35):
    found = []

    def probe(port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            return sock.connect_ex((ip, port)) == 0
        except OSError:
            return False
        finally:
            sock.close()

    for port, is_open in parallel(probe, list(ports), workers=len(ports), timeout=timeout + 1.0).items():
        if is_open:
            found.append(port)
    return sorted(found)


def scan_range(net):
    """The addresses to sweep: the real subnet when small, else the /24 around us."""
    if not net.get("local_ip"):
        return None
    try:
        network = ipaddress.ip_network(net.get("network") or (net["local_ip"] + "/24"), strict=False)
    except ValueError:
        return None
    if network.num_addresses > 1024:
        network = ipaddress.ip_network(net["local_ip"] + "/24", strict=False)
    return network


def merge(net, arp_entries, mdns, ssdp, network):
    """Combine ARP, mDNS and SSDP sightings into one record per address."""
    records = {}

    def record(ip):
        return records.setdefault(ip, {
            "ip": ip, "mac": None, "vendor": None, "random_mac": False, "hostname": None, "name": None,
            "self": False, "gateway": False, "mdns": None, "ssdp": None, "ports": [],
            "rtt_ms": None, "loss_pct": None,
        })

    def inside(ip):
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return network is None or address in network

    for ip, mac, _iface in netinfo.usable_arp(arp_entries, network):
        item = record(ip)
        item["mac"] = mac
    if net.get("local_ip"):
        item = record(net["local_ip"])
        item["self"] = True
        item["mac"] = net.get("mac") or item["mac"]
        item["hostname"] = net.get("hostname")
    if net.get("gateway") and inside(net["gateway"]):
        record(net["gateway"])["gateway"] = True
    for ip, info in (mdns or {}).items():
        if inside(ip):
            record(ip)["mdns"] = info
    for ip, info in (ssdp or {}).items():
        if inside(ip):
            record(ip)["ssdp"] = info
    for item in records.values():
        if item["mac"]:
            item["random_mac"] = oui.is_random(item["mac"])
            item["vendor"] = oui.lookup(item["mac"])
    return records


def finalize(records):
    """Classify and name every record; returns a sorted list."""
    out = []
    for item in records.values():
        item.update(devices_mod.classify(item))
        item["name"] = devices_mod.display_name(item) or item["label"]
        out.append(item)
    out.sort(key=lambda d: tuple(int(p) for p in d["ip"].split(".")))
    return out


def summarize(device_list):
    counts = {}
    for item in device_list:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    infra = [d for d in device_list if d.get("infra")]
    return {
        "total": len(device_list),
        "by_kind": counts,
        "infrastructure": [{"ip": d["ip"], "name": d["name"], "label": d["label"], "kind": d["kind"],
                            "brand": d.get("brand"), "gateway": d["gateway"]} for d in infra],
        "private_addresses": sum(1 for d in device_list if d.get("random_mac") and not d.get("self")),
    }


def run_scan(progress=None, probe_ports=True, ping_devices=True, net=None):
    """Scan the network this computer is on. Returns the scan section of the audit."""
    say = progress or (lambda message: None)
    net = net or netinfo.get_network()
    result = {"network": net, "devices": [], "summary": {"total": 0, "by_kind": {}, "infrastructure": [],
                                                           "private_addresses": 0}, "notes": []}
    network = scan_range(net)
    if network is None:
        result["notes"].append("Could not find this computer's network address. Is it connected?")
        return result
    if net.get("network") and str(network) != net["network"]:
        result["notes"].append("Large network (%s): only %s was swept." % (net["network"], network))

    say("Listening for devices that announce themselves (Bonjour, UPnP)")
    found = {}

    def run_mdns():
        found["mdns"] = discovery.mdns_scan(net.get("local_ip"))

    def run_ssdp():
        found["ssdp"] = discovery.ssdp_scan(net.get("local_ip"))

    threads = [threading.Thread(target=run_mdns), threading.Thread(target=run_ssdp)]
    for thread in threads:
        thread.daemon = True
        thread.start()
    say("Sweeping %s for connected devices" % network)
    netinfo.sweep(network, skip=(net.get("local_ip"),))
    for thread in threads:
        thread.join(timeout=15)
    time.sleep(0.4)
    records = merge(net, netinfo.arp_table(), found.get("mdns"), found.get("ssdp"), network)

    targets = [ip for ip, item in records.items() if not item["self"]]
    if targets:
        say("Looking up names for %d devices" % len(targets))
        for ip, name in parallel(_reverse_name, targets, workers=32, timeout=4.0).items():
            if name:
                records[ip]["hostname"] = name
    if probe_ports and targets:
        say("Checking what kind of device each one is")
        for ip, ports in parallel(_open_ports, targets, workers=24, timeout=20.0).items():
            records[ip]["ports"] = ports or []
    if ping_devices and targets:
        say("Measuring the link to each device")

        def quality(ip):
            return netinfo.ping(ip, count=4, interval=0.2, timeout=1.0, allow_tcp=False)

        for ip, stats in parallel(quality, targets, workers=12, timeout=45.0).items():
            if stats and stats.get("method") == "icmp":
                records[ip]["rtt_ms"] = stats.get("avg")
                records[ip]["loss_pct"] = stats.get("loss_pct")
    result["devices"] = finalize(records)
    result["summary"] = summarize(result["devices"])
    return result
