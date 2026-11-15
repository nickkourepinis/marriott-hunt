"""Find devices that announce themselves: mDNS (Bonjour) and SSDP (UPnP)."""
from __future__ import annotations

import re
import select
import socket
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

from . import dnsmsg
from .netinfo import is_private_ip

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900

SERVICE_TYPES = [
    "_airplay._tcp", "_raop._tcp", "_companion-link._tcp", "_device-info._tcp", "_homekit._tcp", "_hap._tcp",
    "_googlecast._tcp", "_googlezone._tcp", "_androidtvremote2._tcp", "_spotify-connect._tcp", "_sonos._tcp",
    "_amzn-wplay._tcp", "_amzn-alexa._tcp", "_ipp._tcp", "_ipps._tcp", "_printer._tcp", "_pdl-datastream._tcp",
    "_uscan._tcp", "_scanner._tcp", "_smb._tcp", "_afpovertcp._tcp", "_adisk._tcp", "_ssh._tcp", "_sftp-ssh._tcp",
    "_workstation._tcp", "_rfb._tcp", "_http._tcp", "_https._tcp", "_hue._tcp", "_matter._tcp", "_matterc._udp",
    "_meshcop._udp", "_esphomelib._tcp", "_shelly._tcp", "_miio._udp", "_ewelink._tcp", "_dyson_mqtt._tcp",
    "_airport._tcp", "_sleep-proxy._udp", "_apple-mobdev2._tcp", "_nvstream._tcp", "_xbox._tcp",
    "_plexmediasvr._tcp", "_roku-rcp._tcp", "_viziocast._tcp", "_bose._tcp", "_soundtouch._tcp",
    "_axis-video._tcp", "_rtsp._tcp", "_nanoleafapi._tcp", "_lutron._tcp", "_ecobee._tcp", "_wled._tcp",
    "_home-assistant._tcp", "_octoprint._tcp", "_teamviewer._tcp", "_daap._tcp", "_touch-able._tcp",
]


def _service_of(name):
    """'Kitchen._airplay._tcp.local' -> ('Kitchen', '_airplay._tcp')."""
    match = re.search(r"^(?:(.*)\.)?(_[A-Za-z0-9-]+\._(?:tcp|udp))\.local\.?$", name)
    if not match:
        return None, None
    return match.group(1), match.group(2)


class MdnsResult(object):
    def __init__(self):
        self.by_ip = {}

    def entry(self, ip):
        return self.by_ip.setdefault(ip, {"names": [], "services": [], "txt": {}, "hostname": None})

    def add(self, ip, name=None, service=None, txt=None, hostname=None):
        entry = self.entry(ip)
        if name and name not in entry["names"] and len(entry["names"]) < 6:
            entry["names"].append(name)
        if service and service not in entry["services"]:
            entry["services"].append(service)
        if txt:
            for key, value in txt.items():
                if key in ("md", "model", "fn", "n", "am", "ci", "ty", "product", "manufacturer", "rmodel", "mdl",
                           "usb_mdl", "usb_mfg", "nm", "friendlyname", "rs", "vendor", "deviceid"):
                    entry["txt"].setdefault(key, value)
        if hostname and not entry["hostname"]:
            entry["hostname"] = hostname


def _open_mdns_sockets(local_ip):
    sockets = []
    # One-shot socket: replies come straight back to us (RFC 6762 section 5.1).
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        if local_ip:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
            except OSError:
                pass
        sock.bind(("", 0))
        sock.setblocking(False)
        sockets.append((sock, True))
    except OSError:
        pass
    # Shared socket on the mDNS port: also hears devices that only answer by multicast.
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", MDNS_PORT))
        membership = socket.inet_aton(MDNS_GROUP) + socket.inet_aton(local_ip or "0.0.0.0")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        if local_ip:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
            except OSError:
                pass
        sock.setblocking(False)
        sockets.append((sock, False))
    except OSError:
        pass
    return sockets


def _send_questions(sockets, questions):
    for start in range(0, len(questions), 12):
        chunk = questions[start:start + 12]
        for sock, one_shot in sockets:
            packet = dnsmsg.build_query(chunk, qid=0, recursion=False, unicast_response=one_shot)
            try:
                sock.sendto(packet, (MDNS_GROUP, MDNS_PORT))
            except OSError:
                pass


def absorb_mdns(result, source_ip, message, state):
    """Fold one parsed mDNS response into the result. Returns follow-up questions."""
    follow = []
    hosts = {}      # hostname -> ip
    instances = {}  # instance fqdn -> {"service", "label", "target", "txt"}
    for record in message["records"]:
        name = record["name"]
        if record["type"] == dnsmsg.TYPE_A and "address" in record:
            hosts[name.lower()] = record["address"]
        elif record["type"] == dnsmsg.TYPE_PTR and "target" in record:
            if name.lower().startswith("_services._dns-sd._udp"):
                service = record["target"].replace(".local", "").strip(".")
                if service not in state["types"]:
                    state["types"].add(service)
                    follow.append((service + ".local", dnsmsg.TYPE_PTR))
            else:
                label, service = _service_of(record["target"])
                if service:
                    item = instances.setdefault(record["target"].lower(), {})
                    item["service"], item["label"] = service, label
                    if record["target"].lower() not in state["asked"]:
                        state["asked"].add(record["target"].lower())
                        follow.append((record["target"], dnsmsg.TYPE_SRV))
                        follow.append((record["target"], dnsmsg.TYPE_TXT))
        elif record["type"] == dnsmsg.TYPE_SRV and "target" in record:
            label, service = _service_of(name)
            item = instances.setdefault(name.lower(), {})
            item["target"] = record["target"].lower()
            if service:
                item["service"], item["label"] = service, label
        elif record["type"] == dnsmsg.TYPE_TXT and "txt" in record:
            label, service = _service_of(name)
            item = instances.setdefault(name.lower(), {})
            item["txt"] = record["txt"]
            if service:
                item["service"], item["label"] = service, label
    for host, address in hosts.items():
        if is_private_ip(address):
            result.add(address, hostname=host.rstrip("."))
    for fqdn, item in instances.items():
        target = item.get("target")
        ip = hosts.get(target) if target else None
        if ip is None or not is_private_ip(ip):
            ip = source_ip
        result.add(ip, name=item.get("label"), service=item.get("service"), txt=item.get("txt"),
                   hostname=target.rstrip(".") if target and hosts.get(target) == ip else None)
    return follow


def mdns_scan(local_ip=None, duration=4.0):
    """Ask the local network what services exist. Returns {ip: {...}}."""
    result = MdnsResult()
    sockets = _open_mdns_sockets(local_ip)
    if not sockets:
        return result.by_ip
    state = {"types": set(SERVICE_TYPES), "asked": set()}
    questions = [("_services._dns-sd._udp.local", dnsmsg.TYPE_PTR)]
    questions += [(service + ".local", dnsmsg.TYPE_PTR) for service in SERVICE_TYPES]
    _send_questions(sockets, questions)
    deadline = time.time() + duration
    hard_stop = deadline + 4.0
    resend_at = time.time() + 1.0
    resent = False
    pending = []
    try:
        while time.time() < min(deadline, hard_stop):
            readable, _, _ = select.select([s for s, _ in sockets], [], [], 0.25)
            for sock in readable:
                try:
                    data, addr = sock.recvfrom(9000)
                except OSError:
                    continue
                if addr[0] == local_ip:
                    continue
                try:
                    message = dnsmsg.parse_message(data)
                except ValueError:
                    continue
                if not message["response"]:
                    continue
                pending += absorb_mdns(result, addr[0], message, state)
            if pending and len(state["asked"]) < 400:
                _send_questions(sockets, pending[:120])
                pending = []
                deadline = max(deadline, min(time.time() + 1.5, deadline + 1.5))
            if not resent and time.time() >= resend_at:
                _send_questions(sockets, questions)
                resent = True
    finally:
        for sock, _ in sockets:
            try:
                sock.close()
            except OSError:
                pass
    return result.by_ip


# -------------------------------------------------------------------- SSDP

def parse_ssdp_response(text):
    """Headers of one SSDP reply as a lower-cased dict."""
    headers = {}
    lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    return headers


def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_device_description(xml_text, base_url=""):
    """UPnP device description XML -> {friendly_name, manufacturer, model, device_types, services}."""
    info = {"friendly_name": None, "manufacturer": None, "model": None, "model_number": None,
            "device_types": [], "services": []}
    if not xml_text or "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        return info
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return info
    url_base = base_url
    for child in root:
        if _local(child.tag) == "URLBase" and (child.text or "").strip():
            url_base = child.text.strip()

    def walk(node):
        for child in node:
            tag = _local(child.tag)
            text = (child.text or "").strip()
            if tag == "deviceType" and text and text not in info["device_types"]:
                info["device_types"].append(text)
            elif tag == "friendlyName" and text and not info["friendly_name"]:
                info["friendly_name"] = text[:80]
            elif tag == "manufacturer" and text and not info["manufacturer"]:
                info["manufacturer"] = text[:60]
            elif tag == "modelName" and text and not info["model"]:
                info["model"] = text[:60]
            elif tag == "modelNumber" and text and not info["model_number"]:
                info["model_number"] = text[:40]
            elif tag == "service":
                service = {}
                for field in child:
                    service[_local(field.tag)] = (field.text or "").strip()
                if service.get("serviceType") and service.get("controlURL"):
                    info["services"].append({
                        "type": service["serviceType"],
                        "control": urljoin(url_base, service["controlURL"]) if url_base else service["controlURL"],
                    })
            else:
                walk(child)

    walk(root)
    return info


def fetch_text(url, timeout=3.0, limit=512 * 1024, headers=None, data=None):
    """GET (or POST) a small text document from the local network. '' on failure."""
    try:
        request = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": "wifiscout"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return response.read(limit).decode("utf-8", errors="replace")
    except Exception:
        return ""


def ssdp_scan(local_ip=None, duration=3.0, fetch=True):
    """Find UPnP devices. Returns {ip: {server, types, location, description}}."""
    found = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        if local_ip:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
            except OSError:
                pass
        sock.bind(("", 0))
        sock.setblocking(False)
    except OSError:
        return found
    targets = ["ssdp:all", "upnp:rootdevice", "urn:schemas-upnp-org:device:InternetGatewayDevice:1"]
    try:
        for target in targets:
            message = ("M-SEARCH * HTTP/1.1\r\nHOST: %s:%d\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: %s\r\n\r\n"
                       % (SSDP_GROUP, SSDP_PORT, target)).encode("ascii")
            for _ in range(2):
                try:
                    sock.sendto(message, (SSDP_GROUP, SSDP_PORT))
                except OSError:
                    pass
        deadline = time.time() + duration
        while time.time() < deadline:
            readable, _, _ = select.select([sock], [], [], 0.25)
            if not readable:
                continue
            try:
                data, addr = sock.recvfrom(4096)
            except OSError:
                continue
            headers = parse_ssdp_response(data.decode("utf-8", errors="replace"))
            entry = found.setdefault(addr[0], {"server": None, "types": [], "location": None, "description": None})
            if headers.get("server") and not entry["server"]:
                entry["server"] = headers["server"][:120]
            target = headers.get("st")
            if target and target not in entry["types"] and len(entry["types"]) < 40:
                entry["types"].append(target)
            location = headers.get("location")
            if location and urlparse(location).hostname == addr[0]:
                is_gateway = "InternetGatewayDevice" in (target or "")
                if not entry["location"] or is_gateway:
                    entry["location"] = location
    finally:
        sock.close()
    if fetch:
        # Ask every device for its description at once, and do not let a slow one hold up the scan.
        def describe(entry):
            entry["description"] = parse_device_description(fetch_text(entry["location"]), entry["location"])

        workers = []
        for ip, entry in found.items():
            if entry["location"] and is_private_ip(ip):
                worker = threading.Thread(target=describe, args=(entry,))
                worker.daemon = True
                worker.start()
                workers.append(worker)
        give_up_at = time.time() + 4.0
        for worker in workers:
            worker.join(timeout=max(0.0, give_up_at - time.time()))
    return found
