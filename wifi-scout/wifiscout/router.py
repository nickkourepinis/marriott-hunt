"""Router facts: make and model, WAN address, double NAT and carrier-grade NAT."""
from __future__ import annotations

import json
import re
import socket

from . import osutil
from .discovery import fetch_text
from .netinfo import IP_RE, is_cgnat_ip, is_private_ip
from .speed import mask_ip

_SOAP = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
         's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
         '<u:%s xmlns:u="%s"></u:%s></s:Body></s:Envelope>')


def soap_call(control_url, service_type, action):
    """One UPnP action with no arguments. Returns {tag: text} of the reply."""
    body = (_SOAP % (action, service_type, action)).encode("utf-8")
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": '"%s#%s"' % (service_type, action),
        "User-Agent": "wifiscout",
    }
    text = fetch_text(control_url, timeout=3.0, headers=headers, data=body)
    return parse_soap(text)


def parse_soap(text):
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"<(New\w+)>([^<]*)</\1>", text or "")}


def igd_info(ssdp_entry):
    """Ask a UPnP gateway about its internet side. Returns {} when it has no such service."""
    info = {}
    desc = (ssdp_entry or {}).get("description") or {}
    for service in desc.get("services") or []:
        kind = service["type"]
        if "WANIPConnection" in kind or "WANPPPConnection" in kind:
            reply = soap_call(service["control"], kind, "GetExternalIPAddress")
            if reply.get("NewExternalIPAddress"):
                info["wan_ip"] = reply["NewExternalIPAddress"]
            status = soap_call(service["control"], kind, "GetStatusInfo")
            if status.get("NewConnectionStatus"):
                info["connection_status"] = status["NewConnectionStatus"]
            if status.get("NewUptime", "").isdigit():
                info["uptime_s"] = int(status["NewUptime"])
        elif "WANCommonInterfaceConfig" in kind:
            reply = soap_call(service["control"], kind, "GetCommonLinkProperties")
            for key, name in (("NewLayer1DownstreamMaxBitRate", "link_down_bps"),
                              ("NewLayer1UpstreamMaxBitRate", "link_up_bps")):
                if reply.get(key, "").isdigit():
                    info[name] = int(reply[key])
            if reply.get("NewWANAccessType"):
                info["access_type"] = reply["NewWANAccessType"]
    return info


def parse_traceroute(text):
    """First hops of traceroute, tracert or tracepath output -> [ip or None, ...]."""
    hops = {}
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)[:\s]", line)
        if not match or "?" in line.split()[0]:
            continue
        number = int(match.group(1))
        if number < 1 or number > 30 or number in hops:
            continue
        rest = line[match.end():]
        ips = IP_RE.findall(rest)
        hops[number] = ips[0] if ips else None
    return [hops.get(n) for n in range(1, max(hops) + 1)] if hops else []


def trace_first_hops(target="1.1.1.1", max_hops=4):
    if osutil.PLATFORM == "win":
        text = osutil.run(["tracert", "-d", "-h", str(max_hops), "-w", "800", target], timeout=25)[1]
        # The header line "Tracing route to 1.1.1.1 over a maximum of 4 hops" is not a hop.
        text = "\n".join(line for line in text.splitlines() if re.match(r"\s*\d+\s+(\*|<?\d+\s*ms)", line))
        return parse_traceroute(text)
    if osutil.which("traceroute"):
        text = osutil.run(["traceroute", "-n", "-m", str(max_hops), "-q", "1", "-w", "1", target], timeout=20)[1]
        text = "\n".join(line for line in text.splitlines() if not line.lower().startswith("traceroute"))
        return parse_traceroute(text)
    if osutil.which("tracepath"):
        text = osutil.run(["tracepath", "-n", "-m", str(max_hops), target], timeout=20)[1]
        return parse_traceroute(text)
    return []


def _has_admin_page(ip, timeout=0.6):
    for port in (80, 443):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            if sock.connect_ex((ip, port)) == 0:
                return True
        except OSError:
            pass
        finally:
            sock.close()
    return False


def judge_nat(gateway, hops, wan_ip=None, public_ip=None, admin_check=_has_admin_page):
    """Decide whether traffic passes through two home routers or a carrier NAT.

    Returns {"double_nat": True/False/None, "cgnat": True/False/None, "evidence": [...]}.
    """
    verdict = {"double_nat": None, "cgnat": None, "evidence": []}
    upstream = [hop for hop in hops[1:4] if hop] if hops and hops[0] == gateway else [h for h in hops[:3] if h and h != gateway]
    second = upstream[0] if upstream else None
    if wan_ip:
        if is_cgnat_ip(wan_ip):
            verdict["cgnat"] = True
            verdict["evidence"].append("The router's internet address is in the carrier-grade NAT range (100.64.x.x).")
        elif is_private_ip(wan_ip):
            verdict["double_nat"] = True
            verdict["evidence"].append("The router's internet address is itself a private address (%s), "
                                       "so another router sits in front of it." % mask_private(wan_ip))
        else:
            verdict["double_nat"] = False
            if public_ip and public_ip != wan_ip:
                verdict["cgnat"] = True
                verdict["evidence"].append("The router's internet address differs from the address websites see.")
            elif public_ip:
                verdict["cgnat"] = False
    if second:
        if is_cgnat_ip(second):
            if verdict["cgnat"] is None:
                verdict["cgnat"] = True
            verdict["evidence"].append("The second hop (%s) is a carrier-grade NAT address." % second)
        elif is_private_ip(second) and verdict["double_nat"] is None:
            if second.startswith("192.168.") or admin_check(second):
                verdict["double_nat"] = True
                verdict["evidence"].append("The second hop (%s) is another home router." % second)
            else:
                verdict["evidence"].append("The second hop (%s) is a private address, probably the provider's own network." % second)
        elif not is_private_ip(second) and verdict["double_nat"] is None:
            verdict["double_nat"] = False
    if any(is_cgnat_ip(hop) for hop in upstream) and verdict["cgnat"] is None:
        verdict["cgnat"] = True
    return verdict


def mask_private(ip):
    return ip


def google_wifi_status(gateway):
    """Google Wifi and Nest Wifi expose a small status document on the LAN."""
    text = fetch_text("http://%s/api/v1/status" % gateway, timeout=2.5)
    if not text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    system = data.get("system") or {}
    wan = data.get("wan") or {}
    software = data.get("software") or {}
    if not system and not wan:
        return None
    return {
        "model": system.get("modelId") or system.get("deviceId"), "uptime_s": system.get("uptime"),
        "online": wan.get("online"), "link_mbps": wan.get("linkSpeedMbps"),
        "wan_ip": wan.get("localIpAddress"), "firmware": software.get("softwareVersion"),
        "update_required": software.get("updateRequired"),
    }


_BRAND_HINTS = (
    ("netgear", "NETGEAR"), ("orbi", "NETGEAR Orbi"), ("asus", "ASUS"), ("tp-link", "TP-Link"),
    ("tplink", "TP-Link"), ("deco", "TP-Link Deco"), ("linksys", "Linksys"), ("xfinity", "Xfinity gateway"),
    ("comcast", "Xfinity gateway"), ("spectrum", "Spectrum router"), ("at&t", "AT&T gateway"),
    ("arris", "ARRIS"), ("unifi", "Ubiquiti UniFi"), ("ubiquiti", "Ubiquiti"), ("edgeos", "Ubiquiti EdgeRouter"),
    ("pfsense", "pfSense"), ("opnsense", "OPNsense"), ("openwrt", "OpenWrt"), ("luci", "OpenWrt"),
    ("fritz", "FRITZ!Box"), ("synology", "Synology"), ("routeros", "MikroTik"), ("mikrotik", "MikroTik"),
    ("d-link", "D-Link"), ("dlink", "D-Link"), ("tenda", "Tenda"), ("verizon", "Verizon router"),
    ("fios", "Verizon Fios router"), ("google wifi", "Google Wifi"), ("nest wifi", "Google Nest Wifi"),
    ("huawei", "Huawei"), ("zte", "ZTE"), ("technicolor", "Technicolor gateway"), ("sagemcom", "Sagemcom gateway"),
    ("zyxel", "Zyxel"), ("t-mobile", "T-Mobile gateway"), ("starlink", "Starlink router"),
)


def brand_from_text(text):
    low = (text or "").lower()
    for needle, brand in _BRAND_HINTS:
        if needle in low:
            return brand
    return None


def web_identity(gateway):
    """Guess the router brand from its admin page title."""
    text = fetch_text("http://%s/" % gateway, timeout=2.5, limit=65536)
    if not text:
        return None
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    return brand_from_text((title.group(1) if title else "") + " " + text[:4000])


def inspect(net, scan_result=None, public_ip=None, trace=True):
    """Collect everything known about the main router and the path to the internet."""
    gateway = net.get("gateway")
    info = {
        "gateway": gateway, "brand": None, "model": None, "label": None, "upnp": False,
        "wan_ip_masked": None, "wan_ip_private": None, "uptime_s": None, "link_down_mbps": None,
        "link_up_mbps": None, "hops": [], "double_nat": None, "cgnat": None, "evidence": [], "extra_routers": [],
    }
    if not gateway:
        return info
    device = None
    for item in (scan_result or {}).get("devices") or []:
        if item.get("gateway"):
            device = item
        elif item.get("kind") == "router" and not item.get("self"):
            info["extra_routers"].append({"ip": item["ip"], "name": item.get("name"), "label": item.get("label")})
    wan_ip = None
    if device:
        info["label"] = device.get("label")
        info["brand"] = device.get("brand")
        desc = (device.get("ssdp") or {}).get("description") or {}
        if desc.get("model") or desc.get("friendly_name"):
            info["model"] = " ".join(x for x in (desc.get("manufacturer"), desc.get("model"), desc.get("model_number")) if x)[:80]
        if desc.get("services"):
            igd = igd_info(device.get("ssdp"))
            if igd:
                info["upnp"] = True
                wan_ip = igd.get("wan_ip")
                info["uptime_s"] = igd.get("uptime_s")
                if igd.get("link_down_bps"):
                    info["link_down_mbps"] = round(igd["link_down_bps"] / 1e6, 1)
                if igd.get("link_up_bps"):
                    info["link_up_mbps"] = round(igd["link_up_bps"] / 1e6, 1)
    if not info["brand"] or "google" in (info["brand"] or "").lower():
        google = google_wifi_status(gateway)
        if google:
            info["brand"] = "Google"
            info["model"] = info["model"] or ("Google/Nest Wifi %s" % (google.get("model") or "")).strip()
            info["uptime_s"] = info["uptime_s"] or google.get("uptime_s")
            wan_ip = wan_ip or google.get("wan_ip")
    if not info["brand"]:
        info["brand"] = web_identity(gateway)
    if trace:
        info["hops"] = trace_first_hops()
    if wan_ip and wan_ip not in ("0.0.0.0", ""):
        info["wan_ip_private"] = is_private_ip(wan_ip) or is_cgnat_ip(wan_ip)
        info["wan_ip_masked"] = wan_ip if info["wan_ip_private"] else mask_ip(wan_ip)
    else:
        wan_ip = None
    verdict = judge_nat(gateway, info["hops"], wan_ip=wan_ip, public_ip=public_ip)
    info.update(verdict)
    info["hops"] = [hop if (hop is None or is_private_ip(hop) or is_cgnat_ip(hop)) else mask_ip(hop) for hop in info["hops"]]
    return info
