"""Work out what each device on the network is (router, mesh node, TV, plug...)."""
from __future__ import annotations

import re

KIND_LABELS = {
    "router": "Router",
    "mesh": "Mesh Wi-Fi node",
    "ap": "Access point",
    "extender": "Wi-Fi extender",
    "modem": "ISP box",
    "phone": "Phone or tablet",
    "computer": "Computer",
    "tv": "TV",
    "streamer": "Streaming box",
    "speaker": "Speaker",
    "assistant": "Smart speaker or display",
    "console": "Game console",
    "printer": "Printer",
    "camera": "Camera or doorbell",
    "iot": "Smart home device",
    "hub": "Smart home hub",
    "nas": "Storage or media server",
    "unknown": "Unknown",
}

INFRA_KINDS = ("router", "mesh", "ap", "extender", "modem")
# Devices that sit still and stay awake: usable as fixed signal probes.
STATIONARY_KINDS = ("tv", "streamer", "speaker", "assistant", "printer", "camera", "iot", "hub", "nas", "console")

ISP_BOX_VENDORS = (
    "arris", "commscope", "technicolor", "vantiva", "sagemcom", "hitron", "askey", "arcadyan", "humax", "pace ",
    "2wire", "ubee", "compal broadband", "castlenet", "sercomm", "calix", "adtran", "actiontec", "fiberhome",
    "sky uk", "zoom telephonics", "cisco spvtg", "scientific atlanta", "thomson", "smc networks", "greenwave",
    "gemtek", "wistron neweb", "nokia", "zte", "huawei technologies", "kaonmedia", "netgem", "zhone", "dasan",
)

# vendor substring -> (brand, kind when it is not the gateway, or None when ambiguous)
NETWORK_VENDORS = (
    ("eero", "eero", "mesh"),
    ("plume design", "Plume", "mesh"),
    ("ubiquiti", "Ubiquiti", "ap"),
    ("ruckus", "Ruckus", "ap"),
    ("aruba", "Aruba", "ap"),
    ("cisco meraki", "Meraki", "ap"),
    ("engenius", "EnGenius", "ap"),
    ("cambium", "Cambium", "ap"),
    ("mist systems", "Mist", "ap"),
    ("mikrotik", "MikroTik", "router"),
    ("routerboard", "MikroTik", "router"),
    ("netgear", "NETGEAR", None),
    ("tp-link", "TP-Link", None),
    ("asustek", "ASUS", None),
    ("linksys", "Linksys", None),
    ("belkin", "Belkin", None),
    ("d-link", "D-Link", None),
    ("tenda", "Tenda", None),
    ("mercusys", "Mercusys", None),
    ("zyxel", "Zyxel", None),
    ("buffalo", "Buffalo", None),
    ("avm ", "FRITZ!", None),
    ("keenetic", "Keenetic", None),
    ("draytek", "DrayTek", None),
    ("gl technologies", "GL.iNet", None),
    ("cudy", "Cudy", None),
    ("totolink", "TOTOLINK", None),
    ("trendnet", "TRENDnet", None),
    ("peplink", "Peplink", "router"),
    ("synology", "Synology", None),
)

HOMEKIT_CATEGORIES = {
    "2": ("hub", "HomeKit bridge"), "5": ("iot", "Smart bulb"), "7": ("iot", "Smart plug"),
    "8": ("iot", "Smart switch"), "9": ("iot", "Thermostat"), "10": ("iot", "Sensor"),
    "11": ("iot", "Security system"), "12": ("iot", "Door"), "13": ("iot", "Window"),
    "14": ("iot", "Blinds or shades"), "17": ("camera", "Camera"), "18": ("camera", "Video doorbell"),
    "19": ("iot", "Air purifier"), "20": ("iot", "Heater"), "21": ("iot", "Air conditioner"),
    "26": ("speaker", "Speaker"), "27": ("ap", "AirPort base station"), "28": ("iot", "Sprinkler"),
    "31": ("tv", "TV"), "33": ("router", "Router"),
}

HOST_PATTERNS = (
    (r"deco|orbi|velop|zenwifi|eero|nest-?wifi|google-?wifi|amplifi|\brb[rs]\d|satellite", "mesh", "Mesh Wi-Fi node"),
    (r"extender|repeater|\bre\d{3}|\bex\d{4}|range-?ext|wifi-?ext", "extender", "Wi-Fi extender"),
    (r"unifi|\buap\b|\bu[67]-|access-?point", "ap", "Access point"),
    (r"router|gateway|\brt-a[cx]|archer|nighthawk|tl-wr", "router", "Router"),
    (r"iphone|ipad", "phone", "iPhone or iPad"),
    (r"macbook|imac|mac-?mini|mac-?studio|mac-?pro|\bmbp\b|\bmba\b", "computer", "Mac"),
    (r"galaxy|pixel|android|oneplus|redmi|\bmoto", "phone", "Android phone or tablet"),
    (r"apple-?tv", "streamer", "Apple TV"),
    (r"roku", "streamer", "Roku"),
    (r"fire-?tv|firestick|\baft[a-z]{1,4}\b", "streamer", "Fire TV"),
    (r"chromecast|shield|tivo|google-?tv", "streamer", "Streaming box"),
    (r"bravia|webos|lgwebostv|\btv\b|vizio|hisense|\btcl\b|samsung-?tv|frame-?tv", "tv", "Smart TV"),
    (r"homepod", "speaker", "HomePod"),
    (r"echo|alexa", "assistant", "Amazon Echo"),
    (r"nest-?(mini|hub|audio)|google-?(home|nest)", "assistant", "Google Nest speaker or display"),
    (r"sonos|bose|denon|heos|yamaha|soundbar", "speaker", "Speaker"),
    (r"xbox", "console", "Xbox"),
    (r"playstation|\bps[345]\b", "console", "PlayStation"),
    (r"nintendo|steamdeck", "console", "Game console"),
    (r"printer|officejet|laserjet|deskjet|envy|\bhp[0-9a-f]{6}|epson|brother|canon|\bbrn[0-9a-f]", "printer", "Printer"),
    (r"doorbell|ring-|\bcam\b|camera|arlo|wyze|blink|eufy|reolink|hikvision|dahua|amcrest|\bnvr\b|\bdvr\b|nest-?cam",
     "camera", "Camera or doorbell"),
    (r"synology|diskstation|qnap|readynas|mycloud|truenas|unraid|\bnas\b|plex", "nas", "Storage or media server"),
    (r"plug|bulb|light|lamp|thermostat|ecobee|\bhue\b|lifx|kasa|tapo|wemo|shelly|sonoff|tasmota|esp[-_]?[0-9a-f]{4,}"
     r"|esp32|esp8266|tuya|smartlife|meross|govee|roomba|irobot|dyson|myq|rachio|sprinkler|garage|\block\b|sensor"
     r"|blind|shade|\bhs1\d\d|\bkp\d{3}|\bkl\d{3}|\bep\d{2}\b|switch-|washer|dryer|fridge|oven|dishwasher",
     "iot", "Smart home device"),
    (r"desktop-|laptop-|-pc\b|\bpc-|thinkpad|surface|\bwin-|ubuntu|debian|fedora|raspberrypi|\bnuc\b", "computer", "Computer"),
)

VENDOR_FALLBACK = (
    ("sony interactive", "console", "PlayStation"),
    ("nintendo", "console", "Nintendo console"),
    ("valve corp", "console", "Steam Deck"),
    ("meta platforms", "console", "VR headset"),
    ("oculus", "console", "VR headset"),
    ("roku", "streamer", "Roku"),
    ("nvidia", "streamer", "NVIDIA Shield or PC"),
    ("sonos", "speaker", "Sonos speaker"),
    ("bose", "speaker", "Bose speaker"),
    ("harman", "speaker", "Speaker"),
    ("d&m holdings", "speaker", "Denon or Marantz audio"),
    ("yamaha", "speaker", "Yamaha audio"),
    ("sound united", "speaker", "Speaker"),
    ("ring llc", "camera", "Ring camera or doorbell"),
    ("ring inc", "camera", "Ring camera or doorbell"),
    ("wyze", "camera", "Wyze camera"),
    ("arlo", "camera", "Arlo camera"),
    ("hikvision", "camera", "Hikvision camera"),
    ("dahua", "camera", "Dahua camera"),
    ("reolink", "camera", "Reolink camera"),
    ("amcrest", "camera", "Amcrest camera"),
    ("axis communications", "camera", "Axis camera"),
    ("anker innovations", "camera", "eufy or Anker device"),
    ("nest labs", "iot", "Nest thermostat, camera or alarm"),
    ("espressif", "iot", "Smart plug, bulb or sensor (ESP chip)"),
    ("tuya", "iot", "Smart home device (Tuya)"),
    ("ecobee", "iot", "ecobee thermostat"),
    ("lutron", "hub", "Lutron lighting hub"),
    ("signify", "hub", "Philips Hue bridge"),
    ("philips lighting", "hub", "Philips Hue bridge"),
    ("lifi labs", "iot", "LIFX bulb"),
    ("nanoleaf", "iot", "Nanoleaf lights"),
    ("irobot", "iot", "Roomba"),
    ("roborock", "iot", "Robot vacuum"),
    ("ecovacs", "iot", "Robot vacuum"),
    ("chamberlain", "iot", "Garage door opener"),
    ("rachio", "iot", "Sprinkler controller"),
    ("meross", "iot", "Meross smart device"),
    ("allterco", "iot", "Shelly smart device"),
    ("itead", "iot", "Sonoff smart device"),
    ("leedarson", "iot", "Smart bulb"),
    ("sengled", "iot", "Smart bulb"),
    ("yeelink", "iot", "Yeelight bulb"),
    ("august home", "iot", "Smart lock"),
    ("resideo", "iot", "Honeywell Home thermostat"),
    ("honeywell", "iot", "Honeywell device"),
    ("whirlpool", "iot", "Whirlpool appliance"),
    ("haier", "iot", "GE or Haier appliance"),
    ("ge appliances", "iot", "GE appliance"),
    ("dyson", "iot", "Dyson fan or purifier"),
    ("midea", "iot", "Air conditioner or appliance"),
    ("broadlink", "iot", "BroadLink remote hub"),
    ("lumi united", "hub", "Aqara hub"),
    ("wonderlabs", "hub", "SwitchBot hub"),
    ("tesla", "iot", "Tesla car or Powerwall"),
    ("peloton", "iot", "Peloton"),
    ("withings", "iot", "Withings scale or sensor"),
    ("fitbit", "iot", "Fitbit scale"),
    ("garmin", "iot", "Garmin device"),
    ("canon", "printer", "Canon printer"),
    ("seiko epson", "printer", "Epson printer"),
    ("brother industries", "printer", "Brother printer"),
    ("lexmark", "printer", "Lexmark printer"),
    ("xerox", "printer", "Xerox printer"),
    ("kyocera", "printer", "Kyocera printer"),
    ("ricoh", "printer", "Ricoh printer"),
    ("qnap", "nas", "QNAP NAS"),
    ("western digital", "nas", "WD network drive"),
    ("seagate", "nas", "Seagate network drive"),
    ("asustor", "nas", "ASUSTOR NAS"),
    ("raspberry pi", "computer", "Raspberry Pi"),
    ("intel corporate", "computer", "Computer (Intel Wi-Fi)"),
    ("dell", "computer", "Dell computer"),
    ("lenovo", "computer", "Lenovo computer"),
    ("micro-star", "computer", "MSI computer"),
    ("giga-byte", "computer", "Gigabyte computer"),
    ("asrock", "computer", "Desktop PC"),
    ("acer", "computer", "Acer computer"),
    ("framework computer", "computer", "Framework laptop"),
    ("razer", "computer", "Razer computer"),
    ("vmware", "computer", "Virtual machine"),
    ("parallels", "computer", "Virtual machine"),
    ("pcs systemtechnik", "computer", "Virtual machine"),
    ("motorola mobility", "phone", "Motorola phone"),
    ("oneplus", "phone", "OnePlus phone"),
    ("oppo", "phone", "OPPO phone"),
    ("vivo mobile", "phone", "vivo phone"),
    ("huawei device", "phone", "Huawei phone or tablet"),
    ("honor device", "phone", "Honor phone"),
    ("xiaomi", "phone", "Xiaomi phone or smart device"),
    ("realme", "phone", "realme phone"),
    ("hmd global", "phone", "Nokia phone"),
    ("htc corporation", "phone", "HTC phone"),
    ("tct mobile", "phone", "TCL or Alcatel phone"),
    ("tcl king", "tv", "TCL TV"),
    ("hisense", "tv", "Hisense TV"),
    ("vizio", "tv", "Vizio TV"),
    ("skyworth", "tv", "Smart TV"),
    ("lg electronics", "tv", "LG TV or appliance"),
    ("lg innotek", "tv", "LG TV or appliance"),
    ("sony", "tv", "Sony TV or audio"),
    ("amazon technologies", "assistant", "Amazon device (Echo, Fire TV, Kindle or Ring)"),
    ("hewlett packard", "unknown", "HP printer or computer"),
    ("hp inc", "unknown", "HP printer or computer"),
    ("samsung", "unknown", "Samsung phone, TV or appliance"),
    ("google", "unknown", "Google device (Nest, Chromecast or Pixel)"),
    ("apple", "unknown", "Apple device (Mac, Apple TV or HomePod)"),
    ("microsoft", "unknown", "Microsoft device (Xbox or Surface)"),
    ("texas instruments", "iot", "Smart home device (TI chip)"),
    ("silicon laboratories", "iot", "Smart home device"),
    ("azurewave", "unknown", "Device with an AzureWave Wi-Fi module"),
    ("murata", "unknown", "Device with a Murata Wi-Fi module"),
    ("liteon", "unknown", "Device with a Lite-On Wi-Fi module"),
    ("hon hai", "unknown", "Device with a Foxconn Wi-Fi module"),
    ("realtek", "unknown", "Device with a Realtek network chip"),
)


def _result(kind, label, confidence, brand=None):
    return {"kind": kind, "type": KIND_LABELS.get(kind, "Unknown"), "label": label,
            "confidence": confidence, "brand": brand, "infra": kind in INFRA_KINDS}


def _network_brand(vendor):
    for needle, brand, kind in NETWORK_VENDORS:
        if needle in vendor:
            return brand, kind
    return None, None


def _is_isp_vendor(vendor):
    return any(needle in vendor for needle in ISP_BOX_VENDORS)


def _apple_model(model):
    model = model.lower()
    if "appletv" in model:
        return _result("streamer", "Apple TV", "high", "Apple")
    if "audioaccessory" in model:
        return _result("speaker", "HomePod", "high", "Apple")
    if "iphone" in model or "ipad" in model or "ipod" in model:
        return _result("phone", "iPhone or iPad", "high", "Apple")
    if "watch" in model:
        return _result("phone", "Apple Watch", "high", "Apple")
    if "airport" in model or "timecapsule" in model:
        return _result("ap", "Apple AirPort base station", "high", "Apple")
    if re.search(r"mac|book", model):
        return _result("computer", "Mac", "high", "Apple")
    return None


def classify(device):
    """Decide what a device is. `device` is the merged scan record."""
    vendor = (device.get("vendor") or "").lower()
    mdns = device.get("mdns") or {}
    ssdp = device.get("ssdp") or {}
    desc = ssdp.get("description") or {}
    services = set(mdns.get("services") or [])
    txt = mdns.get("txt") or {}
    ports = set(device.get("ports") or [])
    names = [device.get("hostname") or "", mdns.get("hostname") or ""] + list(mdns.get("names") or [])
    names.append(desc.get("friendly_name") or "")
    host = " ".join(n for n in names if n).lower()
    ssdp_types = " ".join((ssdp.get("types") or []) + (desc.get("device_types") or [])).lower()
    ssdp_text = " ".join(str(x) for x in (ssdp.get("server"), desc.get("manufacturer"), desc.get("model"),
                                           desc.get("friendly_name")) if x).lower()
    brand, brand_kind = _network_brand(vendor)
    model = " ".join(str(txt.get(k, "")) for k in ("md", "model", "rmodel", "am", "ty", "usb_mdl", "mdl", "product"))
    model_low = model.lower()

    if device.get("self"):
        return _result("computer", "This computer", "high")

    if device.get("gateway"):
        if brand == "eero":
            return _result("router", "eero gateway (main router)", "high", brand)
        if _is_isp_vendor(vendor):
            maker = (device.get("vendor") or "").split(",")[0].split(" ")[0].title()
            return _result("router", "ISP gateway (modem and router in one), made by %s" % maker, "high", maker)
        if brand:
            return _result("router", "%s router" % brand, "high", brand)
        if "google" in vendor:
            return _result("router", "Google Nest Wifi router", "medium", "Google")
        if "apple" in vendor or "_airport._tcp" in services:
            return _result("router", "Apple AirPort router", "medium", "Apple")
        return _result("router", "Main router", "high", desc.get("manufacturer"))

    # --- network gear that is not the gateway
    if brand_kind == "mesh":
        return _result("mesh", "%s mesh node" % brand, "high", brand)
    if "_airport._tcp" in services:
        return _result("ap", "Apple AirPort base station", "high", "Apple")
    if "nest wifi" in model_low or "google wifi" in model_low:
        return _result("mesh", "Google Nest Wifi point", "high", "Google")
    if "internetgatewaydevice" in ssdp_types:
        return _result("router", "%s router (a second router on this network)" % (brand or desc.get("manufacturer") or "Another"),
                       "high", brand)
    if "wfadevice" in ssdp_types or "wlanaccesspoint" in ssdp_types:
        return _result("ap", "%s access point or mesh node" % (brand or desc.get("manufacturer") or "Wi-Fi"), "medium", brand)
    for pattern, kind, label in HOST_PATTERNS[:4]:
        if re.search(pattern, host):
            return _result(kind, ("%s %s" % (brand, label.lower())) if brand else label, "medium", brand)
    if brand_kind in ("ap", "router"):
        label = "access point" if brand_kind == "ap" else "router"
        return _result(brand_kind, "%s %s" % (brand, label), "medium", brand)
    if brand == "TP-Link":
        if 9999 in ports or re.search(r"kasa|tapo|\bhs\d{3}|\bkp\d{3}|\bkl\d{3}|\bep\d{2}\b|plug|bulb|\bcam", host):
            return _result("iot", "TP-Link Kasa or Tapo smart device", "medium", brand)
        if ports & {53, 80, 443}:
            return _result("ap", "TP-Link router, Deco node or extender", "medium", brand)
        return _result("unknown", "TP-Link device (router, extender or smart plug)", "low", brand)
    if brand == "ASUS":
        if 53 in ports:
            return _result("mesh", "ASUS router or AiMesh node", "medium", brand)
    elif brand == "Synology":
        if 53 in ports and not ports & {5000, 5001, 445}:
            return _result("router", "Synology router", "medium", brand)
        return _result("nas", "Synology NAS", "medium", brand)
    elif brand == "Belkin":
        if ports & {53, 80}:
            return _result("ap", "Belkin or Linksys router", "low", brand)
        return _result("iot", "Belkin Wemo smart device", "low", brand)
    elif brand and ports & {53, 80, 443}:
        return _result("ap", "%s router, mesh node or extender" % brand, "medium", brand)
    if "google" in vendor and 53 in ports and "_googlecast._tcp" not in services:
        return _result("mesh", "Google Nest Wifi point", "low", "Google")

    # --- what the device says about itself (Bonjour)
    if services & {"_ipp._tcp", "_ipps._tcp", "_printer._tcp", "_pdl-datastream._tcp", "_uscan._tcp", "_scanner._tcp"}:
        return _result("printer", (txt.get("ty") or txt.get("usb_mdl") or "Printer")[:50], "high")
    if "_sonos._tcp" in services or "sonos" in vendor:
        return _result("speaker", "Sonos speaker", "high", "Sonos")
    if "_googlecast._tcp" in services:
        if re.search(r"chromecast|google tv", model_low):
            return _result("streamer", model.strip()[:40] or "Chromecast", "high", "Google")
        if re.search(r"home|mini|audio|hub|nest", model_low):
            return _result("assistant", model.strip()[:40] or "Google Nest speaker", "high", "Google")
        if re.search(r"tv|bravia|shield|display", model_low):
            return _result("tv", model.strip()[:40], "high")
        return _result("streamer", model.strip()[:40] or "Cast device", "medium")
    apple = _apple_model(model) if model.strip() else None
    if apple:
        return apple
    if services & {"_hap._tcp", "_homekit._tcp", "_matter._tcp", "_matterc._udp"}:
        category = HOMEKIT_CATEGORIES.get(str(txt.get("ci", "")).strip())
        if category:
            return _result(category[0], category[1], "high")
        if not services & {"_airplay._tcp", "_raop._tcp"}:
            return _result("iot", "Smart home accessory", "medium")
    if services & {"_airplay._tcp", "_raop._tcp"}:
        if re.search(r"tv|qn\d|oled|bravia|roku", model_low + " " + host):
            return _result("tv", "AirPlay TV", "medium")
        if "apple" in vendor:
            return _result("unknown", "Apple device (Mac, Apple TV or HomePod)", "medium", "Apple")
        return _result("speaker", "AirPlay speaker or TV", "medium")
    if "_amzn-wplay._tcp" in services:
        return _result("streamer", "Fire TV", "high", "Amazon")
    if "_amzn-alexa._tcp" in services:
        return _result("assistant", "Amazon Echo", "high", "Amazon")
    if "_roku-rcp._tcp" in services or "roku" in ssdp_types or "roku" in ssdp_text:
        return _result("streamer", "Roku", "high", "Roku")
    if "_hue._tcp" in services:
        return _result("hub", "Philips Hue bridge", "high", "Philips")
    if "_home-assistant._tcp" in services:
        return _result("hub", "Home Assistant server", "high")
    if services & {"_esphomelib._tcp", "_shelly._tcp", "_miio._udp", "_ewelink._tcp", "_wled._tcp", "_dyson_mqtt._tcp",
                   "_nanoleafapi._tcp", "_lutron._tcp", "_ecobee._tcp", "_octoprint._tcp"}:
        return _result("iot", "Smart home device", "high")
    if services & {"_axis-video._tcp", "_rtsp._tcp"}:
        return _result("camera", "Network camera", "high")
    if services & {"_androidtvremote2._tcp", "_viziocast._tcp"}:
        return _result("tv", "Smart TV or streaming box", "high")
    if services & {"_spotify-connect._tcp", "_bose._tcp", "_soundtouch._tcp"}:
        return _result("speaker", "Speaker or audio receiver", "medium")
    if "_plexmediasvr._tcp" in services or 32400 in ports:
        return _result("nas", "Plex media server", "high")
    if "_xbox._tcp" in services:
        return _result("console", "Xbox", "high", "Microsoft")
    if "_apple-mobdev2._tcp" in services:
        return _result("phone", "iPhone or iPad", "high", "Apple")
    if "_meshcop._udp" in services and len(services) == 1:
        return _result("hub", "Thread border router", "medium")

    # --- UPnP
    if "zoneplayer" in ssdp_types:
        return _result("speaker", "Sonos speaker", "high", "Sonos")
    if "printer" in ssdp_types:
        return _result("printer", desc.get("friendly_name") or "Printer", "high")
    if "dial-multiscreen" in ssdp_types or "mediarenderer" in ssdp_types:
        label = desc.get("friendly_name") or desc.get("model") or "Smart TV"
        if re.search(r"speaker|sound|audio|receiver|avr", ssdp_text):
            return _result("speaker", label[:50], "medium")
        return _result("tv", label[:50], "medium")
    if "mediaserver" in ssdp_types:
        return _result("nas", desc.get("friendly_name") or "Media server", "medium")
    if "philips" in ssdp_text and "hue" in ssdp_text:
        return _result("hub", "Philips Hue bridge", "high", "Philips")

    # --- name on the network
    for pattern, kind, label in HOST_PATTERNS[4:]:
        if re.search(pattern, host):
            return _result(kind, label, "medium")
    if services & {"_smb._tcp", "_afpovertcp._tcp", "_adisk._tcp", "_ssh._tcp", "_sftp-ssh._tcp",
                   "_workstation._tcp", "_rfb._tcp", "_companion-link._tcp", "_device-info._tcp"}:
        return _result("computer", "Computer", "medium")

    # --- open ports
    if 62078 in ports:
        return _result("phone", "iPhone or iPad", "high", "Apple")
    if ports & {9100, 631, 515}:
        return _result("printer", "Printer", "medium")
    if 554 in ports:
        return _result("camera", "Camera or video recorder", "medium")
    if ports & {8008, 8009}:
        return _result("streamer", "Cast device (Chromecast or smart TV)", "medium")

    # --- ISP boxes that are not the gateway
    if _is_isp_vendor(vendor):
        return _result("modem", "ISP box (modem, gateway or TV box)", "low")

    # --- maker only
    for needle, kind, label in VENDOR_FALLBACK:
        if needle in vendor:
            return _result(kind, label, "low")
    if brand:
        return _result("unknown", "%s device" % brand, "low", brand)
    if ports & {3389, 445, 22, 5900}:
        return _result("computer", "Computer", "low")
    if device.get("random_mac"):
        return _result("phone", "Phone, tablet or laptop (private Wi-Fi address)", "low")
    if device.get("vendor"):
        return _result("unknown", "Device made by %s" % device["vendor"], "low")
    return _result("unknown", "Unknown device", "low")


def display_name(device):
    """Best human name for a device."""
    mdns = device.get("mdns") or {}
    desc = (device.get("ssdp") or {}).get("description") or {}
    txt = mdns.get("txt") or {}
    candidates = [txt.get("fn"), txt.get("n")]
    candidates += [n for n in (mdns.get("names") or []) if n and not re.fullmatch(r"[0-9A-Fa-f:@_-]{12,}.*", n)]
    candidates += [desc.get("friendly_name"), device.get("hostname"), mdns.get("hostname")]
    for name in candidates:
        if not name:
            continue
        name = re.sub(r"\.(local|lan|home|localdomain|attlocal\.net|hsd1\..*)\.?$", "", str(name).strip())
        name = re.sub(r"^[0-9A-Fa-f]{12}@", "", name)
        if name and not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", name):
            return name[:48]
    return None
