"""Turn measurements into findings and a ranked fix list.

Thresholds follow common Wi-Fi survey practice:
  signal   -65 dBm or better is solid, -72 marginal, -80 unusable for calls
  noise    signal-to-noise under 20 dB behaves like a weak signal
  latency  the router hop should answer in a few ms with almost no jitter
  cable    downstream -7..+7 dBmV ideal (+-15 limit), SNR 33 dB+, upstream 35-49 dBmV
"""
from __future__ import annotations

import math
import re
import time

from . import osutil, solutions
from .devices import STATIONARY_KINDS

GRADES = (("strong", 70), ("fair", 50), ("weak", 30), ("dead", 0))
GRADE_LABEL = {"strong": "Strong", "fair": "Fair", "weak": "Weak", "dead": "Dead zone"}
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3, "good": 4}
SEVERITY_COST = {"critical": 20, "major": 10, "minor": 4}
OUTDOOR_WORDS = re.compile(r"patio|yard|garden|deck|porch|driveway|pool|balcony|terrace|shed|outside|outdoor", re.I)

_SIGNAL_CURVE = ((-95, 0), (-90, 5), (-80, 30), (-72, 50), (-65, 70), (-55, 90), (-45, 100))
_SPEED_CURVE = ((0, 0), (2, 10), (8, 30), (25, 50), (75, 70), (200, 90), (500, 100))
_RTT_CURVE = ((3, 100), (8, 90), (20, 70), (50, 50), (120, 30), (300, 0))
_JITTER_CURVE = ((2, 100), (5, 90), (15, 70), (30, 50), (60, 30), (150, 0))
_LOSS_CURVE = ((0, 100), (1, 70), (3, 50), (5, 30), (10, 10), (25, 0))


def interp(value, curve):
    """Piecewise-linear lookup, clamped at both ends."""
    if value <= curve[0][0]:
        return float(curve[0][1])
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if value <= x1:
            return y0 + (y1 - y0) * (value - x0) / float(x1 - x0)
    return float(curve[-1][1])


def grade_of(score):
    for name, floor in GRADES:
        if score >= floor:
            return name
    return "dead"


def spot_scores(spot, collector_via="ethernet"):
    """Sub-scores (0-100) for one measured spot, plus the overall score and grade.

    When the computer running the survey is itself on Wi-Fi, phone-to-computer
    speed crosses the air twice, so the speed thresholds are halved.
    """
    out = {"signal": None, "speed": None, "latency": None, "overall": None, "grade": None}
    rssi = spot.get("rssi")
    if isinstance(rssi, (int, float)) and rssi < 0:
        signal = interp(rssi, _SIGNAL_CURVE)
        noise = spot.get("noise")
        if isinstance(noise, (int, float)) and -120 < noise < 0:
            snr = rssi - noise
            if snr < 25:
                signal = min(signal, interp(snr, ((5, 0), (10, 20), (15, 40), (20, 60), (25, 80))))
        out["signal"] = round(signal)
    down = spot.get("down_mbps")
    if isinstance(down, (int, float)):
        factor = 2.0 if collector_via == "wifi" else 1.0
        speed = interp(down * factor, _SPEED_CURVE)
        up = spot.get("up_mbps")
        if isinstance(up, (int, float)):
            speed = 0.7 * speed + 0.3 * interp(up * factor, _SPEED_CURVE)
        out["speed"] = round(speed)
    rtt = spot.get("rtt_ms")
    if isinstance(rtt, (int, float)):
        parts = [interp(rtt, _RTT_CURVE)]
        if isinstance(spot.get("jitter_ms"), (int, float)):
            parts.append(interp(spot["jitter_ms"], _JITTER_CURVE))
        if isinstance(spot.get("loss_pct"), (int, float)):
            parts.append(interp(spot["loss_pct"], _LOSS_CURVE))
        out["latency"] = round(0.6 * min(parts) + 0.4 * sum(parts) / len(parts))
    weights = (("signal", 0.40), ("speed", 0.35), ("latency", 0.25))
    present = [(out[key], weight) for key, weight in weights if out[key] is not None]
    if present:
        overall = sum(value * weight for value, weight in present) / sum(weight for _, weight in present)
        overall = min(overall, min(value for value, _ in present) + 25)
        out["overall"] = round(overall)
        out["grade"] = grade_of(out["overall"])
    return out


def node_key(bssid):
    """Radios of one access point share the middle of their address."""
    return bssid[3:14] if bssid and len(bssid) == 17 else None


def _names(items, limit=6):
    names = [str(item) for item in items if item]
    if len(names) > limit:
        return ", ".join(names[:limit]) + " and %d more" % (len(names) - limit)
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0] if names else ""


def _num(value, digits=0):
    if value is None:
        return "?"
    if digits == 0:
        return "%d" % round(value)
    return ("%." + str(digits) + "f") % value


def _distance(a, b):
    if None in (a.get("x"), a.get("y"), b.get("x"), b.get("y")) or a.get("floor") != b.get("floor"):
        return None
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


class Report(object):
    def __init__(self):
        self.findings = []
        self.notes = {}

    def add(self, fid, severity, area, title, detail, evidence=None, fixes=None, where=None, note=None, note_fix=None):
        self.findings.append({
            "id": fid, "severity": severity, "area": area, "title": title, "detail": detail,
            "evidence": [e for e in (evidence or []) if e], "fixes": list(fixes or []), "where": list(where or []),
        })
        if note and fixes:
            self.note(note_fix or fixes[0], note)

    def note(self, fix, text):
        self.notes.setdefault(fix, [])
        if text not in self.notes[fix]:
            self.notes[fix].append(text)


# --------------------------------------------------------------- context

def build_context(bundle):
    """Facts several rules need: topology, brand, home profile."""
    scan = bundle.get("scan") or {}
    devices = scan.get("devices") or []
    profile = dict(bundle.get("profile") or {})
    eero = bundle.get("eero") or {}
    wifi = bundle.get("wifi") or {}
    channels = wifi.get("channels") or {}
    infra = [d for d in devices if d.get("infra")]
    gateway = next((d for d in devices if d.get("gateway")), None)
    mesh_nodes = [d for d in devices if d.get("kind") in ("mesh", "ap") and not d.get("gateway")]
    extenders = [d for d in devices if d.get("kind") == "extender"]
    brand = (gateway or {}).get("brand") or (bundle.get("router") or {}).get("brand")
    is_eero = bool(eero.get("nodes")) or (brand or "").lower() == "eero" or any(
        (d.get("brand") or "").lower() == "eero" for d in infra)
    access_points = 1 + len(mesh_nodes) + len(extenders) if gateway or devices else 0
    if eero.get("nodes"):
        access_points = max(access_points, len(eero["nodes"]))
    survey = bundle.get("survey") or {}
    spots = survey.get("spots") or []
    node_keys = {node_key(s.get("bssid")) for s in spots if s.get("bssid")}
    node_keys.discard(None)
    if len(node_keys) > access_points:
        access_points = len(node_keys)
    density = (channels.get("density") or {}).get("level")
    home = profile.get("home")
    if not home and density in ("high", "very high"):
        home = "apartment"
        profile["home_guessed"] = True
    auto_channel = is_eero or "google" in (brand or "").lower()
    isp_gateway = bool(gateway and "ISP gateway" in (gateway.get("label") or ""))
    return {
        "devices": devices, "gateway": gateway, "infra": infra, "mesh_nodes": mesh_nodes, "extenders": extenders,
        "brand": brand, "is_eero": is_eero, "access_points": access_points, "is_mesh": access_points >= 2,
        "profile": profile, "home": home, "small_home": home in ("apartment", "condo") or (profile.get("sqft") or 9999) < 1200,
        "density": density, "auto_channel": auto_channel, "isp_gateway": isp_gateway,
        "collector_via": survey.get("collector_via") or (bundle.get("network") or {}).get("via") or "unknown",
        "plan_down": profile.get("plan_down"), "plan_up": profile.get("plan_up"),
    }


def coverage_fixes(ctx, outdoor=False):
    if outdoor:
        return ["outdoor_ap", "move_node" if ctx["is_mesh"] else "reposition_router"]
    if ctx["is_mesh"]:
        return ["move_node", "add_node", "wired_backhaul"]
    return ["reposition_router", "add_mesh"]


# ------------------------------------------------------------------ rules

def rules_survey(bundle, ctx, rep):
    survey = bundle.get("survey") or {}
    raw_spots = survey.get("spots") or []
    spots = []
    for spot in raw_spots:
        scores = spot_scores(spot, ctx["collector_via"])
        if scores["overall"] is None:
            continue
        item = dict(spot)
        item["scores"] = scores
        spots.append(item)
    summary = {"spots": len(spots), "coverage_pct": None, "by_floor": [], "nodes": [], "worst": None, "best": None,
               "mean": None}
    if not spots:
        rep.add("no_survey", "info", "coverage", "No room-by-room survey yet",
                "This report only knows the signal where the computer sits. Walk the home with your iPhone to "
                "find the actual dead zones.", fixes=["run_survey"])
        return spots, summary
    ordered = sorted(spots, key=lambda s: s.get("ts") or 0)
    dead = [s for s in spots if s["scores"]["grade"] == "dead"]
    weak = [s for s in spots if s["scores"]["grade"] == "weak"]
    usable = [s for s in spots if s["scores"]["overall"] >= 50]
    summary["coverage_pct"] = round(100.0 * len(usable) / len(spots))
    summary["mean"] = round(sum(s["scores"]["overall"] for s in spots) / len(spots))
    worst = min(spots, key=lambda s: s["scores"]["overall"])
    best = max(spots, key=lambda s: s["scores"]["overall"])
    summary["worst"] = {"name": worst.get("name"), "score": worst["scores"]["overall"]}
    summary["best"] = {"name": best.get("name"), "score": best["scores"]["overall"]}

    def describe(spot):
        bits = []
        if spot.get("rssi") is not None:
            bits.append("%d dBm" % spot["rssi"])
        if spot.get("down_mbps") is not None:
            bits.append("%s Mbps" % _num(spot["down_mbps"]))
        if spot.get("rtt_ms") is not None and spot["rtt_ms"] > 20:
            bits.append("%s ms" % _num(spot["rtt_ms"]))
        if spot.get("loss_pct"):
            bits.append("%s%% loss" % _num(spot["loss_pct"]))
        return "%s (%s)" % (spot.get("name") or "Unnamed spot", ", ".join(bits) or "score %d" % spot["scores"]["overall"])

    def placement_hint(target):
        strong = [s for s in spots if s["scores"]["overall"] >= 70 and s is not target]
        if not strong:
            return None
        placed = [(d, s) for d, s in ((_distance(target, s), s) for s in strong) if d is not None]
        if placed:
            distance, near = min(placed, key=lambda pair: pair[0])
            return "The nearest strong reading to %s is %s, about %d m away: a node there would reach it." % (
                target.get("name") or "the weak spot", near.get("name") or "a strong spot", max(1, round(distance)))
        before = [s for s in ordered if (s.get("ts") or 0) < (target.get("ts") or 0) and s["scores"]["overall"] >= 70]
        if before:
            return "The last strong reading on your walk before %s was %s: a good place for a node." % (
                target.get("name") or "the weak spot", before[-1].get("name") or "a strong spot")
        return None

    indoor_dead = [s for s in dead if not OUTDOOR_WORDS.search(s.get("name") or "")]
    indoor_weak = [s for s in weak if not OUTDOOR_WORDS.search(s.get("name") or "")]
    outdoor_bad = [s for s in dead + weak if OUTDOOR_WORDS.search(s.get("name") or "")]
    if indoor_dead:
        hints = [h for h in (placement_hint(s) for s in indoor_dead[:2]) if h]
        rep.add("dead_zones", "critical", "coverage",
                "Dead zone%s: %s" % ("s" if len(indoor_dead) > 1 else "", _names(s.get("name") for s in indoor_dead)),
                "Wi-Fi is effectively unusable here: video calls drop and pages stall.",
                evidence=[describe(s) for s in indoor_dead] + hints,
                fixes=coverage_fixes(ctx), where=[s.get("name") for s in indoor_dead])
        for hint in hints:
            rep.note("add_node" if ctx["is_mesh"] else "add_mesh", hint)
            if ctx["is_mesh"]:
                rep.note("move_node", hint)
    if indoor_weak:
        hints = [h for h in (placement_hint(s) for s in indoor_weak[:2]) if h] if not indoor_dead else []
        rep.add("weak_zones", "major", "coverage",
                "Weak coverage: %s" % _names(s.get("name") for s in indoor_weak),
                "Browsing works but streaming buffers and calls break up, especially when the network is busy.",
                evidence=[describe(s) for s in indoor_weak] + hints,
                fixes=coverage_fixes(ctx), where=[s.get("name") for s in indoor_weak])
        for hint in hints:
            rep.note("add_node" if ctx["is_mesh"] else "add_mesh", hint)
    if outdoor_bad:
        rep.add("outdoor_gap", "major" if any(s["scores"]["grade"] == "dead" for s in outdoor_bad) else "minor",
                "coverage", "Outdoor areas are poorly covered: %s" % _names(s.get("name") for s in outdoor_bad),
                "Exterior walls stop most of the signal. Indoor nodes rarely reach a yard or garage well.",
                evidence=[describe(s) for s in outdoor_bad], fixes=coverage_fixes(ctx, outdoor=True),
                where=[s.get("name") for s in outdoor_bad])
    if not dead and not weak:
        rep.add("coverage_ok", "good", "coverage", "Coverage is solid in all %d measured spots" % len(spots),
                "Every spot scored fair or better. The weakest was %s." % describe(worst))

    # Per-floor comparison
    floors = {}
    for spot in spots:
        floors.setdefault(spot.get("floor") or "Main floor", []).append(spot["scores"]["overall"])
    floor_means = {name: sum(values) / len(values) for name, values in floors.items()}
    summary["by_floor"] = [{"floor": name, "spots": len(floors[name]), "mean": round(floor_means[name])}
                           for name in sorted(floor_means, key=lambda n: -floor_means[n])]
    if len(floor_means) > 1:
        top = max(floor_means, key=floor_means.get)
        for name, mean in floor_means.items():
            if name != top and mean < 60 and floor_means[top] - mean >= 20:
                rep.add("floor_gap", "major", "coverage", "%s is much weaker than %s" % (name, top),
                        "Average score %d against %d. Floors absorb a lot of signal, more so with radiant heating, "
                        "concrete or ductwork in between." % (round(mean), round(floor_means[top])),
                        fixes=["move_node", "add_node"] if ctx["is_mesh"] else ["add_mesh", "reposition_router"],
                        where=[name],
                        note="Put a node on %s, near the stairwell or directly above or below the router." % name)

    # Which access point served which spot
    groups = {}
    for spot in spots:
        key = node_key(spot.get("bssid"))
        if key:
            groups.setdefault(key, []).append(spot)
    letters = "ABCDEFGHIJKLMNOP"
    for index, (key, members) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        rssi = [m["rssi"] for m in members if m.get("rssi") is not None]
        summary["nodes"].append({
            "label": "Access point %s" % letters[index % len(letters)], "key": key,
            "spots": [m.get("name") for m in members],
            "avg_rssi": round(sum(rssi) / len(rssi)) if rssi else None,
            "avg_score": round(sum(m["scores"]["overall"] for m in members) / len(members)),
        })
    with_radio = [s for s in spots if s.get("rssi") is not None]

    # Strong signal, poor speed
    slow = [s for s in with_radio if s["rssi"] >= -62 and s["scores"]["speed"] is not None and s["scores"]["speed"] < 50]
    if slow:
        fixes = ["wired_backhaul", "move_node", "channel_plan"] if ctx["is_mesh"] else ["channel_plan", "router_upgrade"]
        rep.add("strong_but_slow", "major", "speed",
                "Strong signal but slow in %s" % _names(s.get("name") for s in slow),
                "The phone hears the access point clearly, yet little data gets through. In a mesh that means the "
                "node's own link back to the router is weak; otherwise the channel is congested."
                if ctx["is_mesh"] else
                "The phone hears the router clearly, yet little data gets through. The channel is congested or the "
                "router cannot keep up.",
                evidence=[describe(s) for s in slow], fixes=fixes, where=[s.get("name") for s in slow])

    # Sticky roaming: weak on one access point while another is strong nearby
    sticky = []
    for spot in with_radio:
        if spot["rssi"] >= -70 or not node_key(spot.get("bssid")):
            continue
        for other in with_radio:
            distance = _distance(spot, other)
            if other is spot or distance is None or distance > 6:
                continue
            if other["rssi"] >= -60 and node_key(other.get("bssid")) != node_key(spot["bssid"]):
                sticky.append((spot, other))
                break
    if sticky:
        rep.add("sticky_roaming", "minor", "coverage",
                "The phone clung to a far access point in %s" % _names(s.get("name") for s, _ in sticky),
                "A closer access point was strong a few metres away, but the phone stayed on the old one.",
                evidence=["%s at %d dBm while %s nearby had %d dBm from another access point" % (
                    s.get("name"), s["rssi"], o.get("name"), o["rssi"]) for s, o in sticky],
                fixes=["roaming_assist"], where=[s.get("name") for s, _ in sticky])

    # 2.4 GHz in strong-signal spots
    on_24 = [s for s in with_radio if s.get("channel") and 1 <= s["channel"] <= 14 and s["rssi"] >= -60
             and (s.get("band") in (None, "2.4"))]
    if on_24 and len(on_24) >= max(1, len(with_radio) // 4):
        rep.add("band_24_strong", "minor", "speed",
                "The phone used slow 2.4 GHz in %d spot%s with strong signal" % (len(on_24), "" if len(on_24) == 1 else "s"),
                "At this signal level the faster 5 GHz band should have been chosen.",
                evidence=[describe(s) + ", channel %d" % s["channel"] for s in on_24[:5]], fixes=["band_steering"],
                where=[s.get("name") for s in on_24])

    # Wi-Fi itself slower than the internet plan, even at the best spot
    speeds = [s["down_mbps"] for s in spots if s.get("down_mbps") is not None]
    plan = ctx["plan_down"] or ((bundle.get("speed") or {}).get("down_mbps") if ctx["collector_via"] == "ethernet" else None)
    if speeds and plan and ctx["collector_via"] == "ethernet" and max(speeds) < 0.6 * plan and plan >= 100:
        rep.add("wifi_caps_plan", "major", "speed",
                "Wi-Fi tops out at %s Mbps, well under the %s Mbps line" % (_num(max(speeds)), _num(plan)),
                "Even at the best spot the wireless link is the bottleneck, so no device on Wi-Fi sees the speed "
                "being paid for.",
                evidence=["Best spot: %s" % describe(max(spots, key=lambda s: s.get("down_mbps") or 0))],
                fixes=["router_upgrade", "wire_stationary"])
    if ctx["collector_via"] == "wifi":
        rep.add("collector_on_wifi", "info", "coverage", "Survey speeds are lower bounds",
                "The computer running the survey was on Wi-Fi, so each speed test crossed the air twice. Plug it "
                "into the router with Ethernet for true numbers. Signal readings are unaffected.")
    return spots, summary


def rules_wifi(bundle, ctx, rep):
    wifi = bundle.get("wifi") or {}
    link = wifi.get("link") or {}
    channels = wifi.get("channels") or {}
    bands = channels.get("bands") or {}
    density = channels.get("density") or {}
    auto = ctx["auto_channel"]
    auto_note = ("Your system picks channels on its own: restart the whole network late at night so it re-scans."
                 if auto else None)
    band24 = bands.get("2.4") or {}
    if band24.get("current") is not None:
        if band24["current"] not in (1, 6, 11):
            rep.add("channel_offgrid", "major", "interference",
                    "2.4 GHz is on channel %d, which overlaps its neighbours" % band24["current"],
                    "Only channels 1, 6 and 11 are clear of each other. Channel %d collides with two of them at once."
                    % band24["current"],
                    evidence=["Clearest right now: channel %d" % band24.get("recommended", 1)], fixes=["channel_plan"],
                    note="Set 2.4 GHz to channel %d, 20 MHz wide." % band24.get("recommended", 1))
        elif band24.get("advice") == "move":
            rep.add("channel_24_crowded", "minor", "interference",
                    "2.4 GHz channel %d is crowded" % band24["current"],
                    "Channel %d has less competition right now." % band24["recommended"],
                    evidence=["Competition score %.1f on channel %d against %.1f on channel %d" % (
                        band24.get("current_score") or 0, band24["current"],
                        next((row["score"] for row in band24.get("scores") or [] if row["channel"] == band24["recommended"]), 0),
                        band24["recommended"])],
                    fixes=["channel_plan"], note=auto_note or "Set 2.4 GHz to channel %d." % band24["recommended"])
    band5 = bands.get("5") or {}
    if band5.get("advice") == "move":
        target = band5.get("recommended")
        rep.add("channel_5_crowded", "minor", "interference",
                "5 GHz channels %s are crowded" % band5.get("current_block"),
                "Channels %s are quieter%s." % (target, (" and the radar-shared channels %s are quieter still" %
                                                         band5["recommended_dfs"]) if band5.get("recommended_dfs") else ""),
                fixes=["channel_plan"], note=auto_note or "Set 5 GHz to a channel in %s." % target)
    if density.get("level") in ("high", "very high"):
        rep.add("dense_rf", "minor" if density["level"] == "very high" else "info", "interference",
                "%d other Wi-Fi networks are in range" % density.get("visible", 0),
                "That is a crowded building. Everyone shares the same airtime, most of all on 2.4 GHz, so the fix "
                "is to use bands and channels the neighbours do not.",
                evidence=["%d of them are strong (-70 dBm or better)" % density.get("strong", 0)],
                fixes=["prefer_5_6ghz", "channel_plan", "wire_stationary"])
    if link.get("connected"):
        if link.get("band") == "2.4" and (link.get("width") or 20) >= 40:
            rep.add("wide_24", "minor", "interference", "2.4 GHz is set to 40 MHz wide",
                    "A 40 MHz channel covers most of the 2.4 GHz band and collides with every neighbour. 20 MHz is "
                    "the right width there.", fixes=["channel_plan"], note="Set 2.4 GHz channel width to 20 MHz.")
        security = (link.get("security") or "").lower()
        if security and (security == "open" or "wep" in security or (security.startswith("wpa ") and "wpa2" not in security)
                         or security == "wpa personal"):
            rep.add("weak_security", "major", "security", "The Wi-Fi uses outdated security (%s)" % link["security"],
                    "Anyone in range can read or join this network.", fixes=["security_upgrade"])
        generation = link.get("generation") or ""
        many = len(ctx["devices"]) >= 25
        fast_plan = (ctx["plan_down"] or (bundle.get("speed") or {}).get("down_mbps") or 0) >= 300
        if generation in ("Wi-Fi 4", "Wi-Fi 3 or older"):
            rep.add("old_wifi", "major" if (many or fast_plan) else "minor", "speed",
                    "This computer connects with %s" % generation,
                    "That standard is over a decade old. Either the router or this computer's Wi-Fi card is the "
                    "limit: check what newer devices connect with before buying anything.",
                    evidence=["Link: %s, %s Mbps" % (link.get("phy") or "unknown mode", _num(link.get("tx_rate")))],
                    fixes=["router_upgrade", "client_adapter"])
        elif generation == "Wi-Fi 5" and (len(ctx["devices"]) >= 35 or fast_plan):
            rep.add("wifi5_busy", "minor", "speed", "Wi-Fi 5 with %d devices%s" % (
                len(ctx["devices"]), " on a fast line" if fast_plan else ""),
                "Wi-Fi 5 serves one device at a time per band. With this many devices, or a line this fast, "
                "Wi-Fi 6 or newer is a real improvement.", fixes=["router_upgrade"])
        rssi, snr = link.get("rssi"), link.get("snr")
        if rssi is not None and rssi < -70:
            rep.add("computer_weak", "minor", "coverage",
                    "This computer's own signal is weak (%d dBm)" % rssi,
                    "Speed and latency numbers taken from here understate the network. Move closer to an access "
                    "point or use Ethernet before judging the internet line.")
        if snr is not None and snr < 20 and (rssi or -100) >= -67:
            rep.add("high_noise", "major", "interference",
                    "High background noise (signal-to-noise only %d dB)" % snr,
                    "The signal is fine but something is drowning it out.",
                    evidence=["Signal %d dBm, noise %s dBm" % (rssi, _num(link.get("noise")))],
                    fixes=["noise_sources", "prefer_5_6ghz"])
        if link.get("band") == "2.4" and any(n.get("mine") and n.get("band") in ("5", "6")
                                             for n in wifi.get("neighbors") or []) and (rssi or -100) >= -65:
            rep.add("computer_on_24", "minor", "speed", "This computer is on 2.4 GHz although 5 GHz is available",
                    "It has a strong signal, so it should be on the faster band.", fixes=["band_steering"])


def rules_speed(bundle, ctx, rep):
    latency = bundle.get("latency") or {}
    speed = bundle.get("speed") or {}
    dns = bundle.get("dns") or {}
    via = (bundle.get("network") or {}).get("via")
    on_wifi_note = " This computer was on Wi-Fi, so part of the gap may be the wireless link." if via == "wifi" else ""
    if latency and latency.get("online") is False:
        gateway = latency.get("gateway") or {}
        if gateway.get("median") is None and gateway.get("sent"):
            rep.add("offline_local", "critical", "isp", "This computer cannot reach the router",
                    "The fault is inside the home: Wi-Fi link, cable or the router itself.", fixes=["reposition_router"])
        else:
            rep.add("offline", "critical", "isp", "No internet connection",
                    "The router answers but nothing beyond it does. That points at the modem or the provider.",
                    fixes=["modem_coax", "isp_ticket"])
        return
    down, up = speed.get("down_mbps"), speed.get("up_mbps")
    plan_down, plan_up = ctx["plan_down"], ctx["plan_up"]
    if down is not None:
        if plan_down and down < 0.8 * plan_down and via == "wifi":
            rep.add("below_plan_wifi", "minor", "isp",
                    "Download measured %s Mbps on a %s Mbps plan, over Wi-Fi" % (_num(down), _num(plan_down)),
                    "That is %d%% of the plan, but this computer was on Wi-Fi, so the wireless link may be the "
                    "limit rather than the line. Plug it into the router with a cable and run '%s speed' "
                    "again before judging the provider." % (round(100.0 * down / plan_down), osutil.command_name()))
        elif plan_down and down < 0.8 * plan_down:
            severity = "major" if down < 0.5 * plan_down else "minor"
            rep.add("below_plan", severity, "isp",
                    "Download is %s Mbps on a %s Mbps plan" % (_num(down), _num(plan_down)),
                    "That is %d%% of what is being paid for, measured by cable." % round(100.0 * down / plan_down),
                    fixes=["modem_coax", "isp_ticket"])
        elif not plan_down and down < 25:
            rep.add("slow_line", "major", "isp", "Internet download is only %s Mbps" % _num(down, 1),
                    "Enough for one HD stream, not for a household.%s" % on_wifi_note, fixes=["isp_plan", "isp_ticket"])
        else:
            rep.add("speed_ok", "good", "isp", "Internet speed is healthy: %s down, %s up (Mbps)" % (_num(down), _num(up)),
                    "Measured with %s%s." % (speed.get("server") or "a public test server",
                                              " from %s" % speed["isp"] if speed.get("isp") else ""))
    elif speed.get("errors"):
        rep.add("speedtest_failed", "info", "isp", "The internet speed test could not run",
                "Reason: %s. Try speed.cloudflare.com in a browser." % speed["errors"][0][:120])
    if up is not None:
        if plan_up and up < 0.7 * plan_up:
            rep.add("upload_below_plan", "minor", "isp", "Upload is %s Mbps on a %s Mbps plan" % (_num(up, 1), _num(plan_up)),
                    "Upload matters for video calls, cloud backup and cameras.%s" % on_wifi_note,
                    fixes=["modem_coax", "isp_ticket"])
        elif up < 5:
            rep.add("slow_upload", "major", "isp", "Upload is only %s Mbps" % _num(up, 1),
                    "A single HD video call needs about 3-4 Mbps of upload, so two people calling at once will "
                    "struggle.", fixes=["isp_plan"])
    gateway = latency.get("gateway") or {}
    internet = latency.get("internet") or {}
    if gateway.get("median") is not None and via == "wifi":
        bad = gateway["median"] > 20 or (gateway.get("jitter") or 0) > 10 or (gateway.get("loss_pct") or 0) >= 2
        if bad:
            rep.add("router_hop_slow", "major", "latency",
                    "The link from this computer to the router is unsteady",
                    "This first hop should take 1-5 ms with no loss. Trouble here is inside the home, before the "
                    "internet line is even involved.",
                    evidence=["Router: %s ms typical, %s ms jitter, %s%% loss" % (
                        _num(gateway["median"], 1), _num(gateway.get("jitter"), 1), _num(gateway.get("loss_pct"), 1))],
                    fixes=coverage_fixes(ctx) + ["channel_plan"])
    if internet.get("median") is not None:
        clean_local = gateway.get("median") is None or (gateway["median"] <= 20 and (gateway.get("loss_pct") or 0) < 2)
        loss = internet.get("loss_pct") or 0
        if loss >= 2 and clean_local:
            rep.add("internet_loss", "major", "isp", "%s%% of packets to the internet are lost" % _num(loss, 1),
                    "The hop to the router is clean, so the loss happens at the modem or beyond. Calls and games "
                    "feel this more than any speed number.", fixes=["modem_coax", "isp_ticket"])
        if internet["median"] > 150:
            rep.add("high_latency", "major", "latency", "Internet latency is very high (%s ms)" % _num(internet["median"]),
                    "Normal for satellite, and for a weak cellular link. On cable or fibre it means a routing or "
                    "line problem.", fixes=["isp_ticket"])
        elif internet["median"] > 80:
            rep.add("elevated_latency", "minor", "latency", "Internet latency is on the high side (%s ms)" % _num(internet["median"]),
                    "Fine for streaming and browsing. Video calls and games are noticeably better under 50 ms.",
                    fixes=["isp_ticket"])
        if (internet.get("jitter") or 0) > 30 and clean_local:
            rep.add("jitter", "minor", "latency", "Latency swings a lot (%s ms jitter)" % _num(internet["jitter"]),
                    "Unsteady delay makes voices robotic and games stutter.", fixes=["sqm_qos", "isp_ticket"])
    grade = speed.get("bloat_grade")
    if grade in ("C", "D", "F"):
        eero_sqm = ((bundle.get("eero") or {}).get("network") or {}).get("sqm")
        note = None
        if ctx["is_eero"] and eero_sqm is False:
            note = "On your eero this setting is currently off."
        if down and up:
            note = (note + " " if note else "") + "Use about %d down / %d up Mbps as the limits." % (down * 0.92, max(1, up * 0.92))
        rep.add("bufferbloat", "major" if grade in ("D", "F") else "minor", "latency",
                "Lag under load: bufferbloat grade %s" % grade,
                "Latency rises by %s ms while the line is busy. One person uploading photos is enough to make "
                "everyone else's calls and games stutter." % _num(speed.get("bloat_ms")),
                evidence=["Idle %s ms, under load %s ms" % (_num(speed.get("idle_ms")), _num(max(
                    v for v in (speed.get("loaded_down_ms"), speed.get("loaded_up_ms"), 0) if v is not None)))],
                fixes=["sqm_qos"], note=note)
    elif grade in ("A+", "A", "B"):
        rep.add("bufferbloat_ok", "good", "latency", "Stays responsive under load (grade %s)" % grade,
                "Latency rises only %s ms while the line is busy." % _num(speed.get("bloat_ms")))
    system_ms = dns.get("system_avg_ms")
    best = dns.get("best_public") or {}
    failures = sum(item.get("failures") or 0 for item in dns.get("system") or [])
    samples = sum(item.get("samples") or 0 for item in dns.get("system") or [])
    if samples and failures * 3 >= samples and best.get("avg_ms") is not None:
        rep.add("dns_failing", "major", "latency", "The current DNS server drops lookups",
                "%d of %d test lookups got no answer. Pages hang before loading even when the line is fine." % (failures, samples),
                fixes=["dns_change"])
    elif system_ms is not None and best.get("avg_ms") is not None and system_ms > 60 and best["avg_ms"] < 0.5 * system_ms:
        rep.add("dns_slow", "minor", "latency", "DNS lookups are slow (%s ms)" % _num(system_ms),
                "%s answers in %s ms from here." % (best.get("label"), _num(best["avg_ms"])),
                fixes=["dns_change"], note="%s (%s) was the fastest in this test." % (best.get("label"), best.get("server")))


def rules_modem(bundle, ctx, rep):
    modem = bundle.get("modem") or {}
    status = modem.get("status")
    if status == "login_required":
        rep.add("modem_login", "info", "isp", "The modem's signal page needs a login",
                "Open %s in a browser, sign in (the password is often on the modem's label), save the connection "
                "or status page, then run: %s modem --file saved-page.html" % (
                    modem.get("url") or "http://192.168.100.1", osutil.command_name()))
        return
    if status != "ok":
        return
    if modem.get("kind") == "cellular":
        cell = modem.get("cellular") or {}
        radio = cell.get("5g") or cell.get("4g") or {}
        rsrp, sinr = radio.get("rsrp"), radio.get("sinr")
        if rsrp is None:
            return
        evidence = ["Signal power (RSRP) %s dBm, quality (SINR) %s dB" % (_num(rsrp), _num(sinr))]
        if rsrp < -105 or (sinr is not None and sinr < 3):
            rep.add("cell_signal_poor", "major", "isp", "The 5G/LTE gateway has a poor cell signal",
                    "Its reception limits the speed of the entire home.", evidence=evidence, fixes=["gateway_window"])
        elif rsrp < -95 or (sinr is not None and sinr < 10):
            rep.add("cell_signal_fair", "minor", "isp", "The 5G/LTE gateway's cell signal is only fair",
                    "A better window position usually gains speed.", evidence=evidence, fixes=["gateway_window"])
        else:
            rep.add("cell_signal_ok", "good", "isp", "The 5G/LTE gateway has a good cell signal", evidence[0])
        return
    summary = modem.get("summary") or {}
    problems = []
    severity = "minor"
    low, high = summary.get("down_power_min"), summary.get("down_power_max")
    if low is not None and high is not None:
        if low < -15 or high > 15:
            severity = "major"
            problems.append("Downstream power %s to %s dBmV is outside the +-15 limit" % (_num(low, 1), _num(high, 1)))
        elif low < -10 or high > 10:
            problems.append("Downstream power %s to %s dBmV is outside the comfortable -10..+10 range" % (_num(low, 1), _num(high, 1)))
    snr = summary.get("snr_min")
    if snr is not None:
        if snr < 30:
            severity = "major"
            problems.append("Signal-to-noise as low as %s dB (needs 33 or more)" % _num(snr, 1))
        elif snr < 33:
            problems.append("Signal-to-noise as low as %s dB (33 or more is healthy)" % _num(snr, 1))
    up_high, up_low = summary.get("up_power_max"), summary.get("up_power_min")
    if up_high is not None:
        if up_high >= 51:
            severity = "major"
            problems.append("Upstream power %s dBmV: the modem is shouting to be heard (healthy is 35-49)" % _num(up_high, 1))
        elif up_high > 49:
            problems.append("Upstream power %s dBmV is near the top of the healthy 35-49 range" % _num(up_high, 1))
        elif up_low is not None and up_low < 30:
            problems.append("Upstream power %s dBmV is unusually low" % _num(up_low, 1))
    bad_words = summary.get("uncorrected") or 0
    if bad_words > 5000:
        severity = "major"
        problems.append("%s uncorrectable errors since the modem last restarted" % format(bad_words, ","))
    elif bad_words > 500:
        problems.append("%s uncorrectable errors since the modem last restarted" % format(bad_words, ","))
    if problems:
        rep.add("modem_signal", severity, "isp",
                "The cable signal at the modem is out of range" if severity == "major"
                else "The cable signal at the modem is borderline",
                "These readings come from the modem itself. Bad levels cause slowdowns and drop-outs that look "
                "like Wi-Fi trouble but are not.", evidence=problems, fixes=["modem_coax", "isp_ticket"],
                note="Numbers to quote: " + "; ".join(problems) + ".", note_fix="isp_ticket")
    else:
        rep.add("modem_ok", "good", "isp", "The cable signal at the modem is healthy",
                "%d downstream channels, power %s to %s dBmV, signal-to-noise %s dB or better." % (
                    summary.get("downstream_channels") or 0, _num(low, 1), _num(high, 1), _num(snr, 1)))
    plan = ctx["plan_down"] or (bundle.get("speed") or {}).get("down_mbps") or 0
    if summary.get("docsis") == "3.0" and plan >= 400:
        rep.add("modem_old", "minor", "isp", "The modem is an older DOCSIS 3.0 model on a fast plan",
                "It bonds %d channels and has no DOCSIS 3.1 carrier, which limits plans above a few hundred Mbps."
                % (summary.get("downstream_channels") or 0), fixes=["modem_upgrade"])


def rules_topology(bundle, ctx, rep):
    router = bundle.get("router") or {}
    devices = ctx["devices"]
    if router.get("double_nat"):
        rep.add("double_nat", "major", "config", "Two routers in a row (double NAT)",
                "Traffic is address-translated twice on its way out.",
                evidence=router.get("evidence") or [], fixes=["bridge_mode", "disable_isp_wifi"])
    if router.get("cgnat"):
        rep.add("cgnat", "info", "config", "The provider uses carrier-grade NAT",
                "Many customers share one public address. Everyday use is unaffected, but port forwarding and "
                "hosting game or camera servers will not work. Ask the provider for a public address if needed.",
                evidence=router.get("evidence") or [])
    extra = router.get("extra_routers") or []
    if extra and not router.get("double_nat"):
        rep.add("second_router", "minor", "config", "A second router is on the network: %s" % _names(
            (r.get("name") or r.get("ip")) for r in extra),
            "If it is routing as well as providing Wi-Fi, devices behind it are cut off from the rest of the "
            "home. It should be in access point (bridge) mode.", fixes=["bridge_mode"])
    if ctx["extenders"]:
        rep.add("extenders", "minor", "backhaul", "%d range extender%s in use" % (
            len(ctx["extenders"]), "" if len(ctx["extenders"]) == 1 else "s"),
            "Extenders repeat on the same radio they listen on, which halves speed for everything behind them.",
            evidence=[d.get("name") for d in ctx["extenders"]], fixes=["replace_extender", "wired_backhaul"])
    brands = {(d.get("brand") or "").lower() for d in ctx["mesh_nodes"] + ctx["extenders"] if d.get("brand")}
    gateway_brand = (ctx["brand"] or "").lower()
    foreign = sorted(b for b in brands if b and gateway_brand and b not in gateway_brand and gateway_brand not in b)
    if foreign and not ctx["isp_gateway"]:
        rep.add("mixed_brands", "minor", "backhaul", "Wi-Fi gear from different makers is mixed together",
                "Access points from different systems do not coordinate, so devices do not move between them "
                "cleanly.", evidence=["Main router: %s. Also found: %s" % (ctx["brand"], ", ".join(foreign))],
                fixes=["replace_extender"])
    if ctx["small_home"] and ctx["access_points"] >= 3:
        rep.add("too_many_nodes", "minor", "backhaul", "%d access points in a small home" % ctx["access_points"],
                "They overlap heavily and compete with each other for the same channels.", fixes=["fewer_nodes"])
    total = len(devices)
    if total >= 60:
        rep.add("many_devices", "minor", "devices", "%d devices on the network" % total,
                "Consumer routers start to strain at this count, mostly on 2.4 GHz where smart-home gear lives.",
                fixes=["iot_network", "wire_stationary", "router_upgrade"])
    unstable = [d for d in devices if d.get("kind") in STATIONARY_KINDS and not d.get("self")
                and ((d.get("loss_pct") or 0) >= 25 or (d.get("rtt_ms") or 0) >= 150)]
    if len(unstable) >= 2:
        rep.add("unstable_devices", "minor", "coverage",
                "%d fixed devices have a shaky connection" % len(unstable),
                "These devices sit still, so a poor link means weak coverage where they are. Think of them as "
                "sensors: their locations mark the soft spots.",
                evidence=["%s: %s ms, %s%% loss" % (d.get("name"), _num(d.get("rtt_ms")), _num(d.get("loss_pct")))
                          for d in unstable[:8]],
                fixes=coverage_fixes(ctx), where=[d.get("name") for d in unstable[:8]])
    spots = (bundle.get("survey") or {}).get("spots") or []
    if ctx["isp_gateway"] and not ctx["is_mesh"] and not spots:
        rep.add("isp_box_only", "info", "coverage", "All Wi-Fi comes from the provider's all-in-one box",
                "These units have modest radios and usually sit wherever the cable enters the home. If any room is "
                "weak, a mesh system behind it is the usual cure.", fixes=["run_survey", "add_mesh"])


def rules_eero(bundle, ctx, rep):
    eero = bundle.get("eero") or {}
    nodes = eero.get("nodes") or []
    if not nodes:
        return
    network = eero.get("network") or {}
    leaves = [n for n in nodes if not n.get("gateway")]
    offline = [n for n in nodes if n.get("status") and str(n["status"]).lower() not in ("green", "connected", "online")]
    if offline:
        rep.add("eero_offline", "critical", "backhaul", "eero offline: %s" % _names(n.get("name") for n in offline),
                "Its area is being served by a node much farther away.", fixes=["restart_node", "move_node"],
                where=[n.get("name") for n in offline])
    weak = [n for n in leaves if not n.get("wired") and n.get("mesh_bars") is not None and n["mesh_bars"] <= 2
            and n not in offline]
    middling = [n for n in leaves if not n.get("wired") and n.get("mesh_bars") == 3 and n not in offline]
    if weak:
        rep.add("eero_weak_link", "major", "backhaul",
                "eero with a poor link to the rest of the mesh: %s" % _names(n.get("name") for n in weak),
                "Everything connected to this node is limited by its weak connection back to the router, no matter "
                "how strong the node itself looks on a phone.",
                evidence=["%s: %s of 5 bars, %s devices" % (n.get("name"), n.get("mesh_bars"), _num(n.get("clients"))) for n in weak],
                fixes=["move_node", "wired_backhaul", "moca"], where=[n.get("name") for n in weak])
    elif middling:
        rep.add("eero_fair_link", "minor", "backhaul",
                "eero with only a fair mesh link: %s" % _names(n.get("name") for n in middling),
                "It works, but moving it a little closer to the next node, or wiring it, would speed up everything "
                "behind it.", fixes=["move_node", "wired_backhaul"], where=[n.get("name") for n in middling])
    wireless_leaves = [n for n in leaves if not n.get("wired")]
    if len(wireless_leaves) >= 2 and not weak and len(wireless_leaves) == len(leaves):
        rep.add("eero_all_wireless", "info", "backhaul", "All %d satellite eeros connect wirelessly" % len(leaves),
                "That is how most homes run. Wiring even one of them with Ethernet or coax frees airtime for all "
                "the others.", fixes=["wired_backhaul", "moca"])
    busy = [n for n in nodes if (n.get("clients") or 0) >= 30]
    if busy:
        rep.add("eero_busy", "minor", "devices", "One eero carries most devices: %s" % _names(
            "%s (%d)" % (n.get("name"), n["clients"]) for n in busy),
            "A node with this many devices is a choke point, more so if it is a wireless satellite.",
            fixes=["wire_stationary", "move_node"])
    clients = [c for c in eero.get("clients") or [] if c.get("connected") and c.get("wireless") and c.get("rssi") is not None]
    weak_clients = sorted((c for c in clients if c["rssi"] <= -72), key=lambda c: c["rssi"])
    if weak_clients:
        severity = "major" if len(weak_clients) >= 4 or any(c["rssi"] <= -80 for c in weak_clients) else "minor"
        rep.add("eero_weak_clients", severity, "coverage",
                "%d device%s report weak signal to the eero network" % (len(weak_clients), "" if len(weak_clients) == 1 else "s"),
                "Each device is a fixed sensor. Where these sit is where coverage is thin.",
                evidence=["%s: %d dBm on %s%s" % (c.get("name"), c["rssi"], c.get("node") or "unknown eero",
                                                   " (%s GHz)" % c["band"] if c.get("band") else "")
                          for c in weak_clients[:10]],
                fixes=coverage_fixes(ctx), where=[c.get("name") for c in weak_clients[:10]])
    elif clients:
        rep.add("eero_clients_ok", "good", "coverage", "All %d wireless devices have usable signal" % len(clients),
                "The weakest is %s at %d dBm." % (min(clients, key=lambda c: c["rssi"]).get("name"),
                                                   min(c["rssi"] for c in clients)))
    if network.get("update_available") or any(n.get("update_available") for n in nodes):
        rep.add("eero_update", "minor", "config", "An eero software update is waiting", "", fixes=["firmware_update"])
    if network.get("bridge"):
        rep.add("eero_bridge", "info", "config", "The eero network runs in bridge mode",
                "Another router does the routing. That avoids double NAT, at the cost of eero features such as "
                "device blocking, profiles and smart queue management.")


def rules_monitor(bundle, ctx, rep):
    monitor = bundle.get("monitor") or {}
    if not monitor.get("samples"):
        return
    hours = (monitor.get("duration_min") or 0) / 60.0
    outages = monitor.get("outages") or []
    span = "%.1f hours" % hours if hours >= 1 else "%d minutes" % round(monitor.get("duration_min") or 0)
    upstream = [o for o in outages if o["scope"] == "upstream"]
    local = [o for o in outages if o["scope"] == "local"]

    def clock(stamp):
        return time.strftime("%a %H:%M", time.localtime(stamp))

    if upstream:
        total = sum(o["seconds"] for o in upstream)
        rep.add("isp_outages", "major" if (total >= 60 or len(upstream) >= 3) else "minor", "isp",
                "%d internet drop-out%s in %s" % (len(upstream), "" if len(upstream) == 1 else "s", span),
                "The router stayed reachable each time, so these happened at the modem or the provider. "
                "%d seconds offline in total." % round(total),
                evidence=["%s for %d s" % (clock(o["start"]), round(o["seconds"])) for o in upstream[:8]],
                fixes=["modem_coax", "isp_ticket"])
    if local:
        total = sum(o["seconds"] for o in local)
        rep.add("local_outages", "major", "coverage",
                "%d time%s this computer lost the router in %s" % (len(local), "" if len(local) == 1 else "s", span),
                "The break was inside the home: the Wi-Fi link or the router itself. %d seconds in total." % round(total),
                evidence=["%s for %d s" % (clock(o["start"]), round(o["seconds"])) for o in local[:8]],
                fixes=coverage_fixes(ctx) + ["firmware_update"])
    if not outages and (monitor.get("duration_min") or 0) >= 30:
        rep.add("stable", "good", "isp", "No drop-outs in %s of monitoring" % span,
                "Typical latency %s ms, worst 5%% above %s ms." % (_num(monitor.get("net_ms")), _num(monitor.get("net_p95_ms"))))
    typical, p95 = monitor.get("net_ms"), monitor.get("net_p95_ms")
    if typical and p95 and p95 > 100 and p95 > 3 * typical:
        rep.add("latency_spikes", "minor", "latency", "Latency spikes to %s ms" % _num(p95),
                "Typical is %s ms. Spikes like this feel like brief freezes on calls." % _num(typical), fixes=["sqm_qos"])
    by_hour = monitor.get("by_hour") or []
    evening = [h["net_ms"] for h in by_hour if h["hour"] in (19, 20, 21, 22) and h.get("net_ms") is not None]
    daytime = [h["net_ms"] for h in by_hour if 9 <= h["hour"] <= 16 and h.get("net_ms") is not None]
    if evening and daytime:
        evening_ms, day_ms = sum(evening) / len(evening), sum(daytime) / len(daytime)
        if evening_ms > 1.6 * day_ms and evening_ms - day_ms > 20:
            rep.add("evening_congestion", "minor", "isp", "The internet slows down in the evening",
                    "Latency averages %s ms from 7 to 11 pm against %s ms during the day. That pattern is the "
                    "provider's neighbourhood network filling up." % (_num(evening_ms), _num(day_ms)),
                    fixes=["isp_ticket", "isp_plan"])


# ------------------------------------------------------------- assembly

def build_plan(rep):
    """Rank the fixes referenced by findings: worst problem first, then cheapest."""
    plan = {}
    ranked = sorted(rep.findings, key=lambda f: SEVERITY_RANK[f["severity"]])
    for order, finding in enumerate(ranked):
        if finding["severity"] in ("good",):
            continue
        for position, fix in enumerate(finding["fixes"]):
            entry = solutions.CATALOG.get(fix)
            if not entry:
                continue
            item = plan.setdefault(fix, {
                "id": fix, "title": entry["title"], "kind": entry["kind"], "cost": entry["cost"],
                "effort": entry["effort"], "why": entry["why"],
                "steps": [step.replace("{cmd}", osutil.command_name()) for step in entry["steps"]],
                "products": list(entry.get("products") or []), "for": [], "notes": [],
                "_rank": 9, "_position": 9, "_order": order,
            })
            item["for"].append(finding["title"])
            item["_rank"] = min(item["_rank"], SEVERITY_RANK[finding["severity"]])
            item["_position"] = min(item["_position"], position)
    for fix, notes in rep.notes.items():
        if fix in plan:
            plan[fix]["notes"] = notes
    ordered = sorted(plan.values(), key=lambda p: (p["_rank"], p["_position"], p["_order"],
                                                   solutions.COST_ORDER.get(p["cost"], 5)))
    for index, item in enumerate(ordered):
        item["step"] = index + 1
        del item["_rank"], item["_position"], item["_order"]
    return ordered


def verdict(score, findings):
    critical = [f for f in findings if f["severity"] == "critical"]
    major = [f for f in findings if f["severity"] == "major"]
    minor = [f for f in findings if f["severity"] == "minor"]
    if critical:
        return "Needs attention. Start with: %s." % critical[0]["title"]
    if major:
        return "Works, with %d real problem%s to fix. Start with: %s." % (len(major), "" if len(major) == 1 else "s", major[0]["title"])
    if minor:
        return "Healthy overall. %d small improvement%s available." % (len(minor), "" if len(minor) == 1 else "s")
    return "Healthy. Nothing found that needs fixing."


def letter(score):
    for mark, floor in (("A", 90), ("B", 80), ("C", 65), ("D", 50)):
        if score >= floor:
            return mark
    return "F"


def analyze(bundle):
    """Run every rule. Returns the analysis section of the report."""
    ctx = build_context(bundle)
    rep = Report()
    spots, coverage = rules_survey(bundle, ctx, rep)
    rules_wifi(bundle, ctx, rep)
    rules_speed(bundle, ctx, rep)
    rules_modem(bundle, ctx, rep)
    rules_topology(bundle, ctx, rep)
    rules_eero(bundle, ctx, rep)
    rules_monitor(bundle, ctx, rep)
    if spots and not (bundle.get("scan") or bundle.get("latency") or bundle.get("wifi")):
        rep.add("no_audit", "info", "coverage", "Only the phone readings were analysed",
                "Run the full check ('%s') on the computer to add devices, channels, internet speed and "
                "the modem to this picture." % osutil.command_name())
    findings = sorted(rep.findings, key=lambda f: SEVERITY_RANK[f["severity"]])
    penalty = 0
    counted = {}
    for finding in findings:
        cost = SEVERITY_COST.get(finding["severity"], 0)
        area_hits = counted.get(finding["area"], 0)
        penalty += cost * (0.6 ** area_hits)  # several findings in one area overlap
        if cost:
            counted[finding["area"]] = area_hits + 1
    score = max(5.0, 100.0 - penalty)
    if coverage.get("mean") is not None:
        score = 0.6 * score + 0.4 * coverage["mean"]
    score = int(round(score))
    measured = bool(bundle.get("speed") or bundle.get("latency") or spots or (bundle.get("scan") or {}).get("devices"))
    speed = bundle.get("speed") or {}
    latency = bundle.get("latency") or {}
    channels = ((bundle.get("wifi") or {}).get("channels") or {})
    return {
        "score": score if measured else None,
        "grade": letter(score) if measured else None,
        "score_note": None if spots or not measured else "Coverage not measured yet: this score covers only what the computer could see.",
        "verdict": verdict(score, findings) if measured else "Nothing measured yet.",
        "findings": findings,
        "plan": build_plan(rep),
        "coverage": coverage,
        "spots": [{"id": s.get("id"), "scores": s["scores"]} for s in spots],
        "context": {
            "home": ctx["home"], "home_guessed": bool(ctx["profile"].get("home_guessed")),
            "access_points": ctx["access_points"], "is_mesh": ctx["is_mesh"], "brand": ctx["brand"],
            "is_eero": ctx["is_eero"], "collector_via": ctx["collector_via"],
        },
        "kpis": {
            "down_mbps": speed.get("down_mbps"), "up_mbps": speed.get("up_mbps"),
            "internet_ms": (latency.get("internet") or {}).get("median"),
            "router_ms": (latency.get("gateway") or {}).get("median"),
            "bloat_grade": speed.get("bloat_grade"), "devices": len(ctx["devices"]),
            "neighbors": (channels.get("density") or {}).get("visible"),
            "coverage_pct": coverage.get("coverage_pct"),
        },
        "counts": {name: sum(1 for f in findings if f["severity"] == name) for name in SEVERITY_RANK},
        "tools": solutions.EXTERNAL_TOOLS,
    }
