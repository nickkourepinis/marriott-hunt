"""Local network facts: addresses, gateway, DNS servers, ARP table, ping.

Every parser takes raw command output so it can be tested without the
command being present.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import statistics
import struct
import time

from . import osutil

MAC_RE = re.compile(r"(?i)\b([0-9a-f]{1,2}(?:[:-][0-9a-f]{1,2}){5})\b")
IP_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


def normalize_mac(text):
    """'a4:1:62:0:b:c' or 'A4-01-62-00-0B-0C' -> 'a4:01:62:00:0b:0c'. None if invalid."""
    if not text:
        return None
    parts = re.split(r"[:-]", text.strip())
    if len(parts) != 6:
        return None
    try:
        values = [int(p, 16) for p in parts]
    except ValueError:
        return None
    if any(v < 0 or v > 255 for v in values):
        return None
    return ":".join("%02x" % v for v in values)


def is_private_ip(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.version == 4 and (addr.is_private or addr.is_link_local) and not addr.is_loopback


def is_cgnat_ip(ip):
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


# ---------------------------------------------------------------- gateway

def parse_route_get_mac(text):
    """macOS `route -n get default` -> (gateway, iface)."""
    gateway = iface = None
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if key == "gateway" and IP_RE.fullmatch(value):
            gateway = value
        elif key == "interface":
            iface = value
    return gateway, iface


def parse_ip_route_linux(text):
    """Linux `ip route show default` -> (gateway, iface). Picks the lowest metric."""
    best = None
    for line in text.splitlines():
        match = re.match(r"\s*default\s+via\s+(\S+)(?:.*?\sdev\s+(\S+))?", line)
        if not match or not IP_RE.fullmatch(match.group(1)):
            continue
        metric = re.search(r"\smetric\s+(\d+)", line)
        rank = int(metric.group(1)) if metric else 0
        if best is None or rank < best[0]:
            best = (rank, match.group(1), match.group(2))
    return (best[1], best[2]) if best else (None, None)


def parse_proc_net_route(text):
    """Linux /proc/net/route -> (gateway, iface)."""
    best = None
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8 or fields[1] != "00000000":
            continue
        try:
            flags = int(fields[3], 16)
            gateway = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
            metric = int(fields[6])
        except (ValueError, OSError, struct.error):
            continue
        if not flags & 0x2 or gateway == "0.0.0.0":
            continue
        if best is None or metric < best[0]:
            best = (metric, gateway, fields[0])
    return (best[1], best[2]) if best else (None, None)


def parse_route_print_win(text):
    """Windows `route print -4` -> (gateway, local_ip). Picks the lowest metric."""
    best = None
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[0] == "0.0.0.0" and fields[1] == "0.0.0.0":
            if not IP_RE.fullmatch(fields[2]) or not IP_RE.fullmatch(fields[3]):
                continue
            try:
                metric = int(fields[4])
            except ValueError:
                metric = 9999
            if best is None or metric < best[0]:
                best = (metric, fields[2], fields[3])
    return (best[1], best[2]) if best else (None, None)


# -------------------------------------------------------------- addresses

def parse_ifconfig(text, iface=None):
    """macOS/BSD `ifconfig [iface]` -> list of {iface, ip, prefix, mac}."""
    out = []
    current = None
    mac = None
    for line in text.splitlines():
        head = re.match(r"^(\S+?):\s+flags=", line)
        if head:
            current = head.group(1)
            mac = None
            continue
        stripped = line.strip()
        if stripped.startswith("ether "):
            mac = normalize_mac(stripped.split()[1])
            for item in out:
                if item["iface"] == current and not item["mac"]:
                    item["mac"] = mac
        match = re.match(r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-fA-F]+|\d+\.\d+\.\d+\.\d+)", stripped)
        if match:
            mask = match.group(2)
            if mask.startswith("0x"):
                prefix = bin(int(mask, 16)).count("1")
            else:
                prefix = _mask_to_prefix(mask)
            out.append({"iface": current or iface, "ip": match.group(1), "prefix": prefix, "mac": mac})
    if iface:
        out = [item for item in out if item["iface"] == iface]
    return out


def parse_ip_addr_linux(text):
    """Linux `ip -o -4 addr show` -> list of {iface, ip, prefix}."""
    out = []
    for line in text.splitlines():
        match = re.match(r"\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if match:
            out.append({"iface": match.group(1), "ip": match.group(2), "prefix": int(match.group(3)), "mac": None})
    return out


_WIFI_WORDS = ("wireless", "wi-fi", "wifi", "wlan", "drahtlos", "sans fil", "inal", "senza fili", "draadloos", "tr\u00e5dl")


def parse_ipconfig_win(text):
    """Windows `ipconfig /all` -> list of adapters {name, wifi, ip, mask, gateway, dns, mac}.

    Works by position, so it survives translated Windows installs: the IPv4
    address line is followed by the mask line, section headers are unindented.
    """
    adapters = []
    current = None
    last_key = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if not raw.startswith((" ", "\t")):
            name = raw.strip().rstrip(":")
            current = {
                "name": name,
                "wifi": any(word in name.lower() for word in _WIFI_WORDS),
                "ip": None, "mask": None, "gateway": None, "dns": [], "mac": None,
            }
            adapters.append(current)
            last_key = None
            continue
        if current is None:
            continue
        line = raw.strip()
        if ":" in line and re.search(r"\.\s*:|\s:\s", line):
            key, _, value = line.partition(":")
            key = key.strip(" .").lower()
            value = value.strip()
            last_key = key
        else:
            key, value = None, line
        ips = IP_RE.findall(value)
        mac = MAC_RE.search(value)
        if key is not None:
            if mac and not ips and current["mac"] is None and "-" in value:
                current["mac"] = normalize_mac(mac.group(1))
            elif ips and ("ipv4" in key or key.startswith("ip address") or "ip-adresse" in key or "adresse ip" in key):
                current["ip"] = ips[0]
            elif ips and current["ip"] and current["mask"] is None and ips[0].startswith("255."):
                current["mask"] = ips[0]
            elif "gateway" in key or "passerelle" in key or "puerta" in key:
                if ips:
                    current["gateway"] = ips[0]
            elif "dns" in key and "suffix" not in key and "suffixe" not in key:
                current["dns"].extend(ips)
        elif ips and last_key:
            if ("gateway" in last_key or "passerelle" in last_key or "puerta" in last_key) and not current["gateway"]:
                current["gateway"] = ips[0]
            elif "dns" in last_key:
                current["dns"].extend(ips)
    return adapters


def _mask_to_prefix(mask):
    try:
        return bin(struct.unpack("!L", socket.inet_aton(mask))[0]).count("1")
    except (OSError, struct.error):
        return 24


def udp_local_ip():
    """The address this machine uses to reach the internet. Sends no packets."""
    for target in ("1.1.1.1", "192.168.1.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((target, 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("0."):
                return ip
        except OSError:
            pass
        finally:
            sock.close()
    return None


# -------------------------------------------------------------------- DNS

def parse_scutil_dns(text):
    servers = []
    for match in re.finditer(r"nameserver\[\d+\]\s*:\s*(\S+)", text):
        if IP_RE.fullmatch(match.group(1)) and match.group(1) not in servers:
            servers.append(match.group(1))
    return servers


def parse_resolv_conf(text):
    servers = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "nameserver" and IP_RE.fullmatch(fields[1]):
            if fields[1] not in servers:
                servers.append(fields[1])
    return servers


def parse_resolvectl(text):
    servers = []
    for line in text.splitlines():
        if re.match(r"\s*(Current DNS Server|DNS Servers)\s*:", line):
            for ip in IP_RE.findall(line.split(":", 1)[1]):
                if ip not in servers:
                    servers.append(ip)
    return servers


# -------------------------------------------------------------------- ARP

def parse_arp_bsd(text):
    """macOS `arp -an` -> list of (ip, mac, iface)."""
    out = []
    for line in text.splitlines():
        match = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+(\S+)(?:\s+on\s+(\S+))?", line)
        if not match:
            continue
        mac = normalize_mac(match.group(2))
        if mac:
            out.append((match.group(1), mac, match.group(3)))
    return out


def parse_ip_neigh(text):
    """Linux `ip neigh show` -> list of (ip, mac, iface)."""
    out = []
    for line in text.splitlines():
        match = re.match(r"(\d+\.\d+\.\d+\.\d+)\s+dev\s+(\S+)\s+lladdr\s+(\S+)\s*(.*)", line)
        if not match:
            continue
        if "FAILED" in match.group(4) or "INCOMPLETE" in match.group(4):
            continue
        mac = normalize_mac(match.group(3))
        if mac:
            out.append((match.group(1), mac, match.group(2)))
    return out


def parse_proc_arp(text):
    """Linux /proc/net/arp -> list of (ip, mac, iface)."""
    out = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 6 or fields[2] == "0x0":
            continue
        mac = normalize_mac(fields[3])
        if mac:
            out.append((fields[0], mac, fields[5]))
    return out


def parse_arp_win(text):
    """Windows `arp -a` -> list of (ip, mac, iface_ip)."""
    out = []
    iface = None
    for line in text.splitlines():
        head = re.match(r"\S.*?(\d+\.\d+\.\d+\.\d+)\s+---", line)
        if head:
            iface = head.group(1)
            continue
        match = re.match(r"\s+(\d+\.\d+\.\d+\.\d+)\s+((?:[0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2})\s", line + " ")
        if match:
            out.append((match.group(1), normalize_mac(match.group(2)), iface))
    return out


def usable_arp(entries, network=None):
    """Drop broadcast, multicast and zero entries; keep one MAC per IP."""
    seen = {}
    for ip, mac, iface in entries:
        if not mac or mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"):
            continue
        if int(mac[:2], 16) & 1:
            continue
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_multicast or addr.is_loopback or ip.endswith(".255") and network is None:
            continue
        if network is not None:
            if addr not in network or addr == network.broadcast_address or addr == network.network_address:
                continue
        seen.setdefault(ip, (mac, iface))
    return [(ip, mac, iface) for ip, (mac, iface) in seen.items()]


def arp_table():
    if osutil.PLATFORM == "mac":
        return parse_arp_bsd(osutil.run(["arp", "-an"], timeout=8)[1])
    if osutil.PLATFORM == "win":
        return parse_arp_win(osutil.run(["arp", "-a"], timeout=8)[1])
    entries = parse_ip_neigh(osutil.run(["ip", "neigh", "show"], timeout=8)[1])
    if not entries:
        entries = parse_proc_arp(osutil.read_file("/proc/net/arp"))
    if not entries:
        entries = parse_arp_bsd(osutil.run(["arp", "-an"], timeout=8)[1])
    return entries


def sweep(network, skip=()):
    """Nudge every address in the subnet so live hosts land in the ARP table.

    Sends one tiny UDP datagram per address. No reply is needed: the operating
    system has to resolve each address with ARP before it can send.
    """
    hosts = [str(h) for h in network.hosts() if str(h) not in skip]
    if len(hosts) > 1022:
        return 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sent = 0
    try:
        for _ in range(2):
            for index, host in enumerate(hosts):
                try:
                    sock.sendto(b"\x00", (host, 33434))
                    sent += 1
                except OSError:
                    pass
                if index % 64 == 63:
                    time.sleep(0.05)
            time.sleep(0.6)
    finally:
        sock.close()
    return sent


# ------------------------------------------------------------------- ping

def parse_ping(text, requested=None):
    """Parse ping output from macOS, Linux or Windows (any language).

    Returns {"sent", "received", "times"} where times are milliseconds.
    """
    times = []
    for match in re.finditer(r"(?i)(?:time|zeit|temps|tiempo|tempo|tijd|tid)\s*([=<])\s*([\d.,]+)\s*ms", text):
        value = float(match.group(2).replace(",", "."))
        if match.group(1) == "<":
            value = value / 2.0
        times.append(value)
    if not times:
        for match in re.finditer(r"(?i)([=<])\s*([\d.,]+)\s*ms\s+TTL", text):
            value = float(match.group(2).replace(",", "."))
            times.append(value / 2.0 if match.group(1) == "<" else value)
    sent = received = None
    summary = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received", text)
    if summary:
        sent, received = int(summary.group(1)), int(summary.group(2))
    else:
        win = re.search(r"=\s*(\d+),[^=\n]*=\s*(\d+),[^=\n]*=\s*(\d+)", text)
        if win:
            sent, received = int(win.group(1)), int(win.group(2))
    if sent is None:
        sent = requested if requested is not None else len(times)
    if received is None:
        received = len(times)
    received = min(received, sent) if sent else received
    return {"sent": sent, "received": received, "times": times}


def summarize_times(times, sent=None):
    """Latency statistics. Jitter is the mean change between consecutive replies."""
    sent = sent if sent is not None else len(times)
    stats = {
        "sent": sent,
        "received": min(len(times), sent) if sent else len(times),
        "loss_pct": None, "min": None, "avg": None, "median": None, "max": None, "jitter": None, "p95": None,
    }
    if sent:
        stats["loss_pct"] = round(100.0 * (sent - stats["received"]) / sent, 1)
    if times:
        ordered = sorted(times)
        stats["min"] = round(ordered[0], 2)
        stats["max"] = round(ordered[-1], 2)
        stats["avg"] = round(sum(times) / len(times), 2)
        stats["median"] = round(statistics.median(times), 2)
        stats["p95"] = round(ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))], 2)
        if len(times) > 1:
            diffs = [abs(b - a) for a, b in zip(times, times[1:])]
            stats["jitter"] = round(sum(diffs) / len(diffs), 2)
        else:
            stats["jitter"] = 0.0
    return stats


def tcp_ping(host, count=5, ports=(443, 80, 53), timeout=1.0, interval=0.1):
    """Latency from TCP connect time. Used when ICMP ping is unavailable."""
    port = None
    times = []
    sent = 0
    for candidate in ports:
        start = time.perf_counter()
        try:
            conn = socket.create_connection((host, candidate), timeout=timeout)
            conn.close()
            port = candidate
            times.append((time.perf_counter() - start) * 1000.0)
            sent = 1
            break
        except ConnectionRefusedError:
            # A refusal still proves the host answered: use it as the round trip.
            port = candidate
            times.append((time.perf_counter() - start) * 1000.0)
            sent = 1
            break
        except OSError:
            continue
    if port is None:
        stats = summarize_times([], sent=count)
        stats["method"] = "tcp"
        return stats
    while sent < count:
        time.sleep(interval)
        sent += 1
        start = time.perf_counter()
        try:
            conn = socket.create_connection((host, port), timeout=timeout)
            conn.close()
            times.append((time.perf_counter() - start) * 1000.0)
        except ConnectionRefusedError:
            times.append((time.perf_counter() - start) * 1000.0)
        except OSError:
            pass
    stats = summarize_times(times, sent=sent)
    stats["method"] = "tcp"
    return stats


def ping(host, count=10, interval=0.2, timeout=1.0, allow_tcp=True):
    """Ping a host. Returns latency statistics with a "method" of icmp or tcp."""
    text = ""
    ran_marker = r"\d+\s+packets transmitted|TTL|=\s*\d+,"
    if osutil.which("ping"):
        if osutil.PLATFORM == "win":
            count = min(count, 10)  # Windows waits a full second between pings
            cmd = ["ping", "-n", str(count), "-w", str(int(timeout * 1000)), host]
            budget = count * (timeout + 1.1) + 3
        elif osutil.PLATFORM == "mac":
            cmd = ["ping", "-n", "-c", str(count), "-i", str(interval), "-W", str(int(timeout * 1000)), host]
            budget = count * (interval + timeout) + 3
        else:
            cmd = ["ping", "-n", "-c", str(count), "-i", str(interval), "-W", str(max(1, int(round(timeout)))), host]
            budget = count * (interval + timeout) + 3
        text = osutil.run(cmd, timeout=budget)[1]
        if not re.search(ran_marker, text) and osutil.PLATFORM != "win" and interval < 1:
            # Some systems refuse sub-second intervals for normal users.
            index = cmd.index("-i")
            del cmd[index:index + 2]
            count = max(3, min(count, 5))
            cmd[cmd.index("-c") + 1] = str(count)
            text = osutil.run(cmd, timeout=count * (1 + timeout) + 3)[1]
    parsed = parse_ping(text, requested=count) if text else None
    if parsed and (parsed["times"] or re.search(ran_marker, text)):
        stats = summarize_times(parsed["times"], sent=parsed["sent"] or count)
        stats["method"] = "icmp"
        return stats
    if allow_tcp:
        return tcp_ping(host, count=min(count, 8), timeout=timeout)
    stats = summarize_times([], sent=count)
    stats["method"] = "none"
    return stats


# ------------------------------------------------------------ the summary

def _mac_wifi_devices():
    """macOS: device names that are Wi-Fi ports, e.g. {'en0'}."""
    text = osutil.run(["networksetup", "-listallhardwareports"], timeout=8)[1]
    return parse_hardware_ports(text)


def parse_hardware_ports(text):
    wifi = set()
    port = None
    for line in text.splitlines():
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip().lower()
        elif line.startswith("Device:") and port is not None:
            if "wi-fi" in port or "airport" in port or "wlan" in port:
                wifi.add(line.split(":", 1)[1].strip())
    return wifi


def get_network():
    """Describe this computer's connection to the home network."""
    info = {
        "hostname": socket.gethostname(),
        "local_ip": None, "gateway": None, "iface": None, "prefix": 24, "network": None,
        "mac": None, "dns": [], "via": "unknown",
    }
    platform = osutil.PLATFORM
    if platform == "mac":
        gateway, iface = parse_route_get_mac(osutil.run(["route", "-n", "get", "default"], timeout=8)[1])
        info["gateway"], info["iface"] = gateway, iface
        if iface:
            for item in parse_ifconfig(osutil.run(["ifconfig", iface], timeout=8)[1], iface):
                info["local_ip"], info["prefix"], info["mac"] = item["ip"], item["prefix"], item["mac"]
                break
            info["via"] = "wifi" if iface in _mac_wifi_devices() else "ethernet"
        info["dns"] = parse_scutil_dns(osutil.run(["scutil", "--dns"], timeout=8)[1])
    elif platform == "win":
        gateway, local_ip = parse_route_print_win(osutil.run(["route", "print", "-4"], timeout=10)[1])
        info["gateway"], info["local_ip"] = gateway, local_ip
        adapters = parse_ipconfig_win(osutil.run(["ipconfig", "/all"], timeout=10)[1])
        chosen = None
        for adapter in adapters:
            if local_ip and adapter["ip"] == local_ip:
                chosen = adapter
                break
        if chosen is None:
            for adapter in adapters:
                if adapter["ip"] and adapter["gateway"]:
                    chosen = adapter
                    break
        if chosen:
            info["iface"] = chosen["name"]
            info["local_ip"] = info["local_ip"] or chosen["ip"]
            info["gateway"] = info["gateway"] or chosen["gateway"]
            info["mac"] = chosen["mac"]
            info["dns"] = chosen["dns"]
            info["via"] = "wifi" if chosen["wifi"] else "ethernet"
            if chosen["mask"]:
                info["prefix"] = _mask_to_prefix(chosen["mask"])
    else:
        gateway, iface = parse_ip_route_linux(osutil.run(["ip", "route", "show", "default"], timeout=8)[1])
        if not gateway:
            gateway, iface = parse_proc_net_route(osutil.read_file("/proc/net/route"))
        info["gateway"], info["iface"] = gateway, iface
        for item in parse_ip_addr_linux(osutil.run(["ip", "-o", "-4", "addr", "show"], timeout=8)[1]):
            if item["iface"] == iface:
                info["local_ip"], info["prefix"] = item["ip"], item["prefix"]
                break
        if iface:
            mac = osutil.read_file("/sys/class/net/%s/address" % iface).strip()
            info["mac"] = normalize_mac(mac)
            if os.path.isdir("/sys/class/net/%s/wireless" % iface) or os.path.isdir("/sys/class/net/%s/phy80211" % iface):
                info["via"] = "wifi"
            elif os.path.isdir("/sys/class/net/%s" % iface):
                info["via"] = "ethernet"
        servers = parse_resolv_conf(osutil.read_file("/etc/resolv.conf"))
        if not servers or all(s.startswith("127.") for s in servers):
            upstream = parse_resolvectl(osutil.run(["resolvectl", "status"], timeout=8)[1])
            servers = upstream or servers
        info["dns"] = servers
    if not info["local_ip"]:
        info["local_ip"] = udp_local_ip()
    if info["local_ip"]:
        try:
            info["network"] = str(ipaddress.ip_network("%s/%d" % (info["local_ip"], info["prefix"]), strict=False))
        except ValueError:
            info["network"] = None
    return info
