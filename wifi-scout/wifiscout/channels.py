"""Channel congestion scoring from the list of networks a device can hear."""
from __future__ import annotations

# 80 MHz blocks of the 5 GHz band. DFS blocks share spectrum with radar: routers
# must leave the channel when radar is detected, so they are quiet but can blip.
BLOCKS_5GHZ = [
    {"name": "36-48", "channels": (36, 40, 44, 48), "dfs": False},
    {"name": "52-64", "channels": (52, 56, 60, 64), "dfs": True},
    {"name": "100-112", "channels": (100, 104, 108, 112), "dfs": True},
    {"name": "116-128", "channels": (116, 120, 124, 128), "dfs": True},
    {"name": "132-144", "channels": (132, 136, 140, 144), "dfs": True},
    {"name": "149-161", "channels": (149, 153, 157, 161), "dfs": False},
    {"name": "165", "channels": (165, 169, 173, 177), "dfs": False},
]

# How much a 20 MHz network N channels away bleeds into ours on 2.4 GHz.
_OVERLAP_24 = {0: 1.0, 1: 0.75, 2: 0.5, 3: 0.25, 4: 0.05}


def signal_weight(rssi):
    """0.05 (barely audible) to 1.0 (same room) for a neighbour at this strength."""
    if rssi is None:
        return 0.3
    return max(0.05, min(1.0, (rssi + 90) / 40.0))


def block_of(channel):
    for block in BLOCKS_5GHZ:
        if channel in block["channels"]:
            return block
    for block in BLOCKS_5GHZ:
        if block["channels"][0] <= channel <= block["channels"][-1] + 2:
            return block
    return None


def score_24(channel, networks):
    total = 0.0
    for item in networks:
        other = item.get("channel")
        if other is None:
            continue
        distance = abs(channel - other)
        if (item.get("width") or 20) >= 40:
            distance = max(0, distance - 4)
        total += signal_weight(item.get("rssi")) * _OVERLAP_24.get(distance, 0.0)
    return round(total, 2)


def density_level(count):
    if count <= 5:
        return "low"
    if count <= 15:
        return "medium"
    if count <= 30:
        return "high"
    return "very high"


def analyze(link, neighbors):
    """Summarise airtime competition per band and suggest better channels.

    Networks flagged "mine" (the user's own mesh nodes) are left out of the
    competition score: they coordinate with each other.
    """
    link = link or {}
    others = [n for n in (neighbors or []) if not n.get("mine") and n.get("channel") is not None]
    audible = [n for n in others if (n.get("rssi") if n.get("rssi") is not None else -80) >= -85]
    strong = [n for n in audible if (n.get("rssi") or -100) >= -70]
    result = {
        "density": {"visible": len(audible), "strong": len(strong), "level": density_level(len(audible))},
        "bands": {},
        "mine": sum(1 for n in (neighbors or []) if n.get("mine")),
    }
    own_channel = link.get("channel") if link.get("connected") else None
    own_band = link.get("band") if link.get("connected") else None

    # 2.4 GHz
    nets24 = [n for n in audible if n.get("band") == "2.4"]
    if nets24 or own_band == "2.4":
        scores = {ch: score_24(ch, nets24) for ch in range(1, 12)}
        counts = {}
        for item in nets24:
            counts[item["channel"]] = counts.get(item["channel"], 0) + 1
        best = min((1, 6, 11), key=lambda ch: (scores[ch], abs(ch - (own_channel or 6))))
        band = {
            "networks": len(nets24), "recommended": best,
            "scores": [{"channel": ch, "score": scores[ch], "networks": counts.get(ch, 0)} for ch in range(1, 12)],
            "current": own_channel if own_band == "2.4" else None, "current_score": None, "advice": None,
            "off_grid": sorted({n["channel"] for n in nets24 if n["channel"] not in (1, 6, 11)}),
        }
        if band["current"] is not None and band["current"] in scores:
            band["current_score"] = scores[band["current"]]
            if band["current"] not in (1, 6, 11):
                band["advice"] = "move"
            elif scores[best] + 0.75 < band["current_score"] * 0.7:
                band["advice"] = "move"
            else:
                band["advice"] = "keep"
                band["recommended"] = band["current"]
        result["bands"]["2.4"] = band

    # 5 GHz
    nets5 = [n for n in audible if n.get("band") == "5"]
    if nets5 or own_band == "5":
        blocks = []
        for block in BLOCKS_5GHZ:
            score = 0.0
            count = 0
            for item in nets5:
                home = block_of(item["channel"])
                spans = [home] if home else []
                if (item.get("width") or 0) >= 160 and home:
                    index = BLOCKS_5GHZ.index(home)
                    partner = index + 1 if index % 2 == 0 else index - 1
                    if 0 <= partner < len(BLOCKS_5GHZ) - 1:
                        spans.append(BLOCKS_5GHZ[partner])
                if block in spans:
                    score += signal_weight(item.get("rssi"))
                    count += 1
            blocks.append({"name": block["name"], "dfs": block["dfs"], "score": round(score, 2), "networks": count})
        usable = [b for b in blocks if b["name"] != "165"]
        best_plain = min((b for b in usable if not b["dfs"]), key=lambda b: b["score"])
        best_dfs = min((b for b in usable if b["dfs"]), key=lambda b: b["score"])
        current_block = block_of(own_channel) if own_band == "5" and own_channel else None
        band = {
            "networks": len(nets5), "blocks": blocks, "recommended": best_plain["name"],
            "recommended_dfs": best_dfs["name"] if best_dfs["score"] + 0.5 < best_plain["score"] else None,
            "current": own_channel if own_band == "5" else None,
            "current_block": current_block["name"] if current_block else None,
            "current_score": None, "advice": None,
        }
        if current_block:
            here = next(b for b in blocks if b["name"] == current_block["name"])
            band["current_score"] = here["score"]
            if best_plain["score"] + 0.75 < here["score"] * 0.7 and best_plain["name"] != here["name"]:
                band["advice"] = "move"
            else:
                band["advice"] = "keep"
                if not current_block["dfs"] or here["score"] <= best_plain["score"]:
                    band["recommended"] = here["name"]
        result["bands"]["5"] = band

    nets6 = [n for n in audible if n.get("band") == "6"]
    if nets6 or own_band == "6":
        result["bands"]["6"] = {"networks": len(nets6), "current": own_channel if own_band == "6" else None}
    return result
