"""Catalog of fixes. Each finding points at one or more of these by id.

cost:   free | $ (under 50) | $$ (50-200) | $$$ (200-600) | $$$$ (600+)   (US dollars, rough)
effort: 5 min | 15 min | 1 hour | half day
Product names are examples of the category, current as of 2026: check what is sold today.
"""
from __future__ import annotations

CATALOG = {
    "reposition_router": {
        "title": "Move the main router to a better spot",
        "kind": "placement", "cost": "free", "effort": "15 min",
        "why": "Position is the cheapest fix there is. Every wall, floor and appliance between the router and a "
               "room costs signal, and a router in a corner sends half its signal outdoors.",
        "steps": [
            "Put it as close to the middle of the home as the cable allows, on the floor where you use Wi-Fi most.",
            "Raise it to shelf height, out in the open. Not in a cabinet, not behind the TV, not on the floor.",
            "Keep it about 1 m (3 ft) away from microwaves, cordless phone bases, fish tanks, mirrors and big metal objects.",
            "If the modem's cable outlet is in a bad corner, run a longer Ethernet cable from the modem to the router "
            "and move only the router.",
        ],
    },
    "move_node": {
        "title": "Reposition the mesh node that covers the weak area",
        "kind": "placement", "cost": "free", "effort": "15 min",
        "why": "A mesh node repeats whatever signal it receives. Parked inside the dead zone it repeats a bad signal; "
               "it has to sit where the signal is still strong and reach forward from there.",
        "steps": [
            "Place each node roughly halfway between the main router and the weak area, no more than two rooms "
            "or about 10 m (30 ft) from the node it talks to.",
            "Aim for line of sight through doorways or along a hallway rather than through bathrooms, kitchens or brick.",
            "Between floors, put the upstairs node near the stairwell or directly above the router.",
            "After moving it, wait 5 minutes and measure the same spots again.",
        ],
    },
    "add_node": {
        "title": "Add a mesh node toward the weak area",
        "kind": "hardware", "cost": "$$", "effort": "15 min",
        "why": "When the existing nodes are already well placed and an area is still weak, the home needs one more "
               "access point to carry the signal there.",
        "steps": [
            "Buy an add-on unit from the same family as your current system so it joins as one network.",
            "Place it at the last spot that still measured strong on the way to the weak area, not in the weak area.",
            "If an Ethernet or coax outlet is nearby, wire the new node. A wired node is far faster than a wireless one.",
        ],
        "products": ["eero: any current eero (eero 7, Pro 7) joins an existing eero network",
                     "Google Nest Wifi Pro point, TP-Link Deco unit, NETGEAR Orbi satellite, ASUS AiMesh router"],
    },
    "add_mesh": {
        "title": "Replace or extend the single router with a mesh system",
        "kind": "hardware", "cost": "$$$", "effort": "1 hour",
        "why": "One router cannot cover this layout. A mesh system puts two or three access points around the home "
               "that act as one network, so devices move between them without dropping.",
        "steps": [
            "Size it: one unit per 1,500 sq ft (140 m2), and at least one per floor.",
            "Choose a tri-band Wi-Fi 6E or Wi-Fi 7 kit. The extra band carries traffic between nodes so your devices "
            "do not share airtime with it.",
            "Keep your ISP box but turn its Wi-Fi off (or set it to bridge mode) so the two do not compete.",
            "Put the first unit at the modem and the others halfway toward the weak areas.",
        ],
        "products": ["eero Pro 7 or eero Max 7 kit", "TP-Link Deco BE series", "NETGEAR Orbi 770 / 870 / 970",
                     "ASUS ZenWiFi BT series", "Google Nest Wifi Pro (Wi-Fi 6E)",
                     "Ubiquiti UniFi (U7 access points) if you are comfortable with more setup"],
    },
    "wired_backhaul": {
        "title": "Connect mesh nodes with Ethernet (wired backhaul)",
        "kind": "wiring", "cost": "$", "effort": "1 hour",
        "why": "A wireless mesh node spends part of its airtime relaying. Wiring it gives that node the full speed "
               "of the router and is the single biggest upgrade a mesh system can get.",
        "steps": [
            "Run Ethernet (Cat 6 or better) from the main router to the node. Flat cable hides under baseboards and carpet edges.",
            "Plug it into any Ethernet port on both ends. eero, Deco, Orbi, Nest and AiMesh detect wired backhaul on their own.",
            "Need more ports? A plain unmanaged gigabit or 2.5G switch between them is fine.",
            "No Ethernet in the walls? Use the coax option below, or powerline as the last resort.",
        ],
        "products": ["Cat 6 Ethernet cable (flat cable for along baseboards)", "8-port unmanaged gigabit or 2.5G switch"],
    },
    "moca": {
        "title": "Use the TV coax wiring as Ethernet (MoCA adapters)",
        "kind": "wiring", "cost": "$$", "effort": "1 hour",
        "why": "Most homes already have coax to several rooms. MoCA 2.5 adapters turn that cable into a 1-2.5 Gbps "
               "link, close to real Ethernet, without opening walls.",
        "steps": [
            "Put one adapter at the router (coax in, Ethernet to the router) and one in each room that needs a wired node.",
            "Fit a MoCA 'PoE' filter where the cable line enters the home so the signal stays inside.",
            "Replace old splitters with ones rated to at least 1675 MHz.",
            "Works alongside cable internet and cable TV. Not compatible with satellite TV wiring.",
        ],
        "products": ["goCoax MA2500D", "ScreenBeam ECB7250", "Translite TL-MC84", "MoCA PoE filter"],
    },
    "powerline": {
        "title": "Powerline adapters, when no Ethernet or coax is available",
        "kind": "wiring", "cost": "$$", "effort": "15 min",
        "why": "Powerline sends data over electrical wiring. Results vary a lot with the wiring, typically 50-300 Mbps, "
               "but it can still beat a weak wireless hop through thick walls.",
        "steps": [
            "Plug both adapters straight into wall outlets, never into a power strip or surge protector.",
            "Outlets on the same circuit breaker work best.",
            "Buy from a store with easy returns and test before committing.",
        ],
        "products": ["TP-Link AV2000 kit (TL-PA9020P)", "devolo Magic 2"],
    },
    "outdoor_ap": {
        "title": "Add a weatherproof access point for outdoor areas",
        "kind": "hardware", "cost": "$$$", "effort": "1 hour",
        "why": "Exterior walls, brick and low-E window glass block Wi-Fi very effectively. Yards, patios and detached "
               "garages need an access point outside or at a window facing them.",
        "steps": [
            "Mount an outdoor-rated unit on the exterior wall facing the area, wired with Ethernet (most are powered over it).",
            "Cheaper first try: move an indoor node to a window sill facing the area.",
        ],
        "products": ["eero Outdoor 7", "TP-Link Deco outdoor units", "Ubiquiti UniFi U7 Outdoor"],
    },
    "replace_extender": {
        "title": "Replace range extenders with mesh nodes or wire them",
        "kind": "hardware", "cost": "$$", "effort": "1 hour",
        "why": "A plug-in extender listens and repeats on the same radio, which roughly halves speed, and devices "
               "often cling to it or to the router at the wrong moment.",
        "steps": [
            "If the extender has an Ethernet port, wire it to the router and switch it to access point mode.",
            "Otherwise retire it and use a mesh node from the same family as the router.",
        ],
    },
    "channel_plan": {
        "title": "Move to a clearer Wi-Fi channel",
        "kind": "setting", "cost": "free", "effort": "5 min",
        "why": "Neighbouring networks on the same or overlapping channels take turns with yours. A quieter channel "
               "gives your devices more airtime.",
        "steps": [
            "On 2.4 GHz use only channel 1, 6 or 11, with 20 MHz width. Anything else overlaps two of them.",
            "On 5 GHz pick the least-used block. The DFS channels (52-144) are usually empty in apartment buildings.",
            "eero and Google Nest choose channels themselves: restart the whole network at a quiet time so they re-scan.",
        ],
    },
    "prefer_5_6ghz": {
        "title": "Lean on 5 GHz and 6 GHz in a crowded building",
        "kind": "setting", "cost": "free", "effort": "15 min",
        "why": "2.4 GHz has room for only three channels and every neighbour shares them. 5 GHz and especially "
               "6 GHz have far more room and do not travel as far through walls, so neighbours intrude less.",
        "steps": [
            "Keep phones, laptops, TVs and consoles on 5 or 6 GHz; leave 2.4 GHz to smart plugs and sensors.",
            "Put the router in the room where you use the fastest devices, since 6 GHz barely passes one wall.",
            "If your router is 2.4/5 GHz only and the building is packed, a Wi-Fi 6E or 7 router opens the 6 GHz band.",
        ],
    },
    "band_steering": {
        "title": "Turn on band steering",
        "kind": "setting", "cost": "free", "effort": "5 min",
        "why": "Devices sometimes sit on slow 2.4 GHz even next to the router. Band steering nudges capable devices "
               "to 5 or 6 GHz.",
        "steps": [
            "Use one network name for all bands and enable 'band steering' or 'Smart Connect' in the router app.",
            "eero: look for 'Client steering' in the app under Settings > Network settings (Advanced).",
            "For a stubborn device: forget the network on it and rejoin while standing near the router.",
        ],
    },
    "roaming_assist": {
        "title": "Help devices switch nodes sooner",
        "kind": "setting", "cost": "free", "effort": "5 min",
        "why": "Phones decide for themselves when to change access points and often hold a fading one too long. "
               "The 802.11k/v/r roaming aids tell them a better one is nearby.",
        "steps": [
            "Enable 'fast roaming', '802.11r' or 'client steering' in the router or mesh app.",
            "Check that nodes are not too close together: if every node is loud everywhere, devices have no reason to move.",
            "Quick manual fix when it happens: switch Wi-Fi off and on at that spot.",
        ],
    },
    "fewer_nodes": {
        "title": "Use fewer mesh nodes",
        "kind": "placement", "cost": "free", "effort": "15 min",
        "why": "More nodes is not more Wi-Fi. In a small home extra nodes interfere with each other and add relay "
               "hops, and devices bounce between them.",
        "steps": [
            "Unplug one node, wait 10 minutes, and measure the same spots again.",
            "Keep the smallest set that leaves every room at 'fair' or better.",
        ],
    },
    "noise_sources": {
        "title": "Find the non-Wi-Fi interference",
        "kind": "placement", "cost": "free", "effort": "15 min",
        "why": "The signal is strong but the background noise is high, so the link behaves like a weak one. Noise "
               "usually comes from something nearby that is not a Wi-Fi network.",
        "steps": [
            "Usual suspects: microwave ovens, baby monitors, cordless phones, wireless cameras, Bluetooth speakers, "
            "USB 3 hubs and drives next to a laptop, cheap LED drivers.",
            "Switch them off one at a time and re-run the scan.",
            "Move the router or the device to 5 GHz, which most of these do not touch.",
        ],
    },
    "sqm_qos": {
        "title": "Turn on smart queue management (fix lag under load)",
        "kind": "setting", "cost": "free", "effort": "5 min",
        "why": "When someone uploads or downloads at full speed, the router queues packets and everything else lags: "
               "calls stutter and games spike. Smart queue management keeps the queue short.",
        "steps": [
            "eero: Settings > Network settings > 'Optimize for conferencing and gaming'.",
            "ASUS: Adaptive QoS or CAKE. UniFi: Smart Queues. NETGEAR: QoS with your real speeds. OpenWrt: SQM with cake.",
            "Set the limit to about 90-95% of the speeds measured here, not the advertised plan.",
            "If the router has no such feature, that is a good reason to replace it.",
        ],
    },
    "dns_change": {
        "title": "Switch to a faster DNS resolver",
        "kind": "setting", "cost": "free", "effort": "5 min",
        "why": "Every page load starts with DNS lookups. A slow resolver adds a pause before anything loads even when "
               "the connection itself is fast.",
        "steps": [
            "In the router's internet or DHCP settings set DNS to 1.1.1.1 and 1.0.0.1 (Cloudflare), "
            "8.8.8.8 and 8.8.4.4 (Google) or 9.9.9.9 (Quad9).",
            "Setting it on the router covers every device. Restart Wi-Fi on devices to pick it up.",
        ],
    },
    "bridge_mode": {
        "title": "Remove the double NAT",
        "kind": "setting", "cost": "free", "effort": "15 min",
        "why": "Two routers in a row each translate addresses. That breaks game consoles' open NAT, port forwarding, "
               "some video calls and VPNs, and adds a little delay.",
        "steps": [
            "Best: put the ISP box into bridge or passthrough mode so your own router is the only router.",
            "Xfinity: Gateway > At a glance > Bridge mode. AT&T: Firewall > IP Passthrough. Spectrum and Verizon "
            "routers: use your own router instead, or set yours to access point mode.",
            "Alternative: set your own router or mesh to bridge / access point mode and let the ISP box route.",
            "Either way, switch off the Wi-Fi on the box that is no longer routing.",
        ],
    },
    "disable_isp_wifi": {
        "title": "Turn off the extra Wi-Fi network",
        "kind": "setting", "cost": "free", "effort": "5 min",
        "why": "Two routers both broadcasting Wi-Fi in the same home fight for the same channels, and devices that "
               "remember both end up on the worse one.",
        "steps": [
            "In the ISP box's admin page or app, disable both the 2.4 and 5 GHz radios.",
            "Forget the old network name on your devices so they do not rejoin it.",
        ],
    },
    "modem_coax": {
        "title": "Fix the cable signal reaching the modem",
        "kind": "wiring", "cost": "free", "effort": "15 min",
        "why": "The modem's own readings are outside the healthy range. That causes slow speeds, dropouts and "
               "packet loss that no Wi-Fi change can fix.",
        "steps": [
            "Hand-tighten every coax connector between the wall and the modem.",
            "Remove splitters. If one must stay, use a single good 2-way splitter and give the modem the first output.",
            "Replace kinked, chewed or very old coax, and try a different wall outlet.",
            "If the readings are still off, call the provider and read them the numbers from this report. A line "
            "problem outside the home is theirs to fix, at no charge.",
        ],
    },
    "modem_upgrade": {
        "title": "Replace the modem with a DOCSIS 3.1 model",
        "kind": "hardware", "cost": "$$", "effort": "15 min",
        "why": "An older DOCSIS 3.0 modem cannot carry today's faster plans and copes worse with busy evening hours.",
        "steps": [
            "Check your provider's approved modem list before buying.",
            "Activate it through the provider's app or support line; the old modem's rental fee stops when you return it.",
        ],
        "products": ["ARRIS S34 or SB8200", "NETGEAR CM2000 / CM3000", "Motorola MB8611"],
    },
    "gateway_window": {
        "title": "Move the 5G / LTE gateway to where the cell signal is strongest",
        "kind": "placement", "cost": "free", "effort": "15 min",
        "why": "With cellular home internet the gateway's own reception sets the speed for the whole home.",
        "steps": [
            "Try windows on the upper floor, on the side of the home facing the tower.",
            "Use the gateway's app signal meter, wait a minute at each spot, and keep the best one.",
            "If that spot is bad for Wi-Fi, leave the gateway there and add a mesh node wired or placed mid-home.",
            "Still weak? Ask the provider about an external antenna, or compare another carrier.",
        ],
    },
    "isp_ticket": {
        "title": "Open a ticket with the internet provider",
        "kind": "service", "cost": "free", "effort": "15 min",
        "why": "The home network checks out and the fault is on the provider's side of the modem.",
        "steps": [
            "Give them specifics from this report: measured speeds against the plan, packet loss, outage times, modem signal levels.",
            "Ask for a line test and a technician visit if levels are out of range.",
            "Run '{cmd} monitor --hours 24' for a day beforehand: a timestamped outage log is hard to argue with.",
        ],
    },
    "isp_plan": {
        "title": "Change the internet plan",
        "kind": "service", "cost": "$$", "effort": "15 min",
        "why": "The Wi-Fi delivers what the line provides. The line itself is the limit for this household.",
        "steps": [
            "Rough guide: 25 Mbps per 4K stream, 10 Mbps per video call, and at least 10-20 Mbps upload for calls and cloud backup.",
            "Compare fibre if it is available at your address: it brings equal upload and steadier latency.",
        ],
    },
    "router_upgrade": {
        "title": "Upgrade the router to Wi-Fi 6E or Wi-Fi 7",
        "kind": "hardware", "cost": "$$$", "effort": "1 hour",
        "why": "The router's Wi-Fi is the bottleneck: it is slower than the internet line or struggles with the "
               "number of devices.",
        "steps": [
            "Wi-Fi 6 handles many devices at once far better than Wi-Fi 5. Wi-Fi 6E and 7 add the uncrowded 6 GHz band.",
            "Match it to the plan: above 1 Gbps look for a 2.5 Gbps internet port.",
            "Old devices keep working on a new router; they simply do not get the new speeds.",
        ],
        "products": ["eero 7 / Pro 7 / Max 7", "TP-Link Archer BE or Deco BE series", "ASUS RT-BE series",
                     "NETGEAR Nighthawk RS series"],
    },
    "wire_stationary": {
        "title": "Wire the devices that never move",
        "kind": "wiring", "cost": "$", "effort": "15 min",
        "why": "Every TV, console and desktop on a cable is one less device competing for airtime, and it gets a "
               "faster, steadier connection than any Wi-Fi.",
        "steps": [
            "Plug TVs, streaming boxes, consoles and desktop PCs into the router or the nearest mesh node's Ethernet port.",
            "A small switch at the TV stand serves several devices from one cable.",
        ],
    },
    "client_adapter": {
        "title": "Upgrade the Wi-Fi adapter in the slow device",
        "kind": "hardware", "cost": "$", "effort": "15 min",
        "why": "An old computer's Wi-Fi card can be the weak link even on a modern network.",
        "steps": ["Add a USB Wi-Fi 6E or Wi-Fi 7 adapter, or wire the device.", "Update the Wi-Fi driver first: it is free and sometimes enough."],
    },
    "iot_network": {
        "title": "Give smart-home devices their own lane",
        "kind": "setting", "cost": "free", "effort": "15 min",
        "why": "Dozens of plugs, bulbs and cameras chatter constantly on 2.4 GHz and slow that band for everything else.",
        "steps": [
            "Leave 2.4 GHz to the smart devices and keep phones and computers on 5 or 6 GHz.",
            "If the router offers an IoT or guest network, move smart devices there. It also keeps them away from your computers.",
        ],
    },
    "security_upgrade": {
        "title": "Switch the Wi-Fi to WPA2 or WPA3 security",
        "kind": "setting", "cost": "free", "effort": "5 min",
        "why": "Open, WEP and original WPA networks can be read or joined by anyone nearby. Besides the privacy "
               "risk, freeloaders use your bandwidth, and old security modes cap speeds at Wi-Fi 3 levels.",
        "steps": ["In the router's wireless settings choose WPA2-Personal (AES) or WPA2/WPA3 mixed.", "Set a new, long password and rejoin your devices."],
    },
    "firmware_update": {
        "title": "Install the pending router update",
        "kind": "setting", "cost": "free", "effort": "5 min",
        "why": "Updates fix stability and security problems, including drop-outs and roaming bugs.",
        "steps": ["Apply the update from the router or mesh app at a quiet time; the network restarts for a few minutes."],
    },
    "restart_node": {
        "title": "Bring the offline node back",
        "kind": "placement", "cost": "free", "effort": "5 min",
        "why": "A node that is offline leaves its whole area on a far-away node.",
        "steps": ["Check its power and cable, then unplug it for 30 seconds.", "If it keeps dropping, it is too far from its neighbour: move it closer or wire it."],
    },
    "run_survey": {
        "title": "Walk the home with your iPhone to map coverage",
        "kind": "measure", "cost": "free", "effort": "15 min",
        "why": "This computer only knows the signal where it sits. The phone survey measures every room and "
               "shows exactly where coverage falls off.",
        "steps": ["Run '{cmd} survey', scan the QR code with the iPhone camera, and tap Measure in each room."],
    },
    "run_monitor": {
        "title": "Log the connection for a day to catch drop-outs",
        "kind": "measure", "cost": "free", "effort": "5 min",
        "why": "Intermittent problems rarely show up in a one-minute test.",
        "steps": ["Run '{cmd} monitor --hours 24' on a computer that stays on, then rebuild the report."],
    },
}

COST_ORDER = {"free": 0, "$": 1, "$$": 2, "$$$": 3, "$$$$": 4}

# Handy tools that are not part of this program.
EXTERNAL_TOOLS = [
    {"name": "AirPort Utility (iPhone, free, Apple)", "use": "Lists every nearby network with its signal and channel. "
     "Turn on Settings > Apps > AirPort Utility > Wi-Fi Scanner, then tap 'Wi-Fi Scan' in the app."},
    {"name": "Shortcuts app (iPhone, built in)", "use": "'Get Network Details' reads the real signal strength (RSSI), "
     "noise, channel and link speed. The phone survey uses it."},
    {"name": "Wireless Diagnostics (Mac, built in)", "use": "Hold Option and click the Wi-Fi icon > Open Wireless "
     "Diagnostics > Window > Scan or Performance."},
    {"name": "networkQuality (Mac, built in)", "use": "Run it in Terminal for speed plus responsiveness under load."},
    {"name": "netsh wlan show wlanreport (Windows, built in)", "use": "Three-day history of every Wi-Fi disconnect and why."},
    {"name": "eero app > Activity and Settings > Troubleshooting", "use": "Per-device usage, a health check and each "
     "node's connection quality."},
    {"name": "Your modem's status page (http://192.168.100.1)", "use": "Signal levels and error counts for cable modems."},
    {"name": "speed.cloudflare.com, fast.com, speedtest.net", "use": "Second opinions on internet speed."},
    {"name": "Waveform bufferbloat test", "use": "Grades how much the connection lags while busy."},
    {"name": "iperf3", "use": "Measures speed between two of your own computers, taking the internet out of the picture."},
    {"name": "NetSpot, WiFi Explorer (Mac), inSSIDer or Acrylic (Windows)", "use": "Dedicated survey and channel "
     "analysers if you want to go deeper."},
    {"name": "Fing (iPhone and Android)", "use": "Device inventory and outage alerts from the phone."},
]
