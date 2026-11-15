import json
import time
import unittest

from wifiscout import analyze, demo, eero, monitor, solutions, speed


def bundle(**parts):
    base = {"meta": {}, "profile": {}, "network": {"via": "ethernet"}, "scan": None, "wifi": None, "latency": None,
            "dns": None, "speed": None, "router": None, "modem": None, "eero": None, "monitor": None,
            "survey": {"floors": ["Main floor"], "spots": [], "collector_via": "ethernet"}}
    base.update(parts)
    return base


def spot(name, rssi=None, down=None, rtt=None, x=None, y=None, floor="Main floor", ts=0, **extra):
    item = {"id": name.lower().replace(" ", "-"), "name": name, "floor": floor, "x": x, "y": y, "ts": ts,
            "rssi": rssi, "down_mbps": down, "up_mbps": None, "rtt_ms": rtt, "jitter_ms": None, "loss_pct": None}
    item.update(extra)
    return item


def ids(result):
    return [f["id"] for f in result["findings"]]


def plan_ids(result):
    return [p["id"] for p in result["plan"]]


class ScoreTests(unittest.TestCase):
    def test_signal_thresholds(self):
        grade = lambda rssi: analyze.spot_scores({"rssi": rssi})["grade"]
        self.assertEqual([grade(v) for v in (-50, -65, -66, -72, -73, -80, -81, -90)],
                         ["strong", "strong", "fair", "fair", "weak", "weak", "dead", "dead"])

    def test_noise_drags_a_strong_signal_down(self):
        clean = analyze.spot_scores({"rssi": -55, "noise": -95})["signal"]
        noisy = analyze.spot_scores({"rssi": -55, "noise": -68})["signal"]
        self.assertGreater(clean, 85)
        self.assertLess(noisy, 40)

    def test_one_bad_metric_caps_the_overall_score(self):
        scores = analyze.spot_scores({"rssi": -45, "down_mbps": 1.0, "rtt_ms": 4})
        self.assertLess(scores["overall"], 45)

    def test_wifi_collector_halves_speed_thresholds(self):
        wired = analyze.spot_scores({"down_mbps": 40}, "ethernet")["speed"]
        wireless = analyze.spot_scores({"down_mbps": 40}, "wifi")["speed"]
        self.assertGreater(wireless, wired)

    def test_no_data_means_no_grade(self):
        self.assertIsNone(analyze.spot_scores({"name": "x"})["grade"])

    def test_interp_clamps(self):
        curve = ((0, 0), (10, 100))
        self.assertEqual(analyze.interp(-5, curve), 0)
        self.assertEqual(analyze.interp(5, curve), 50)
        self.assertEqual(analyze.interp(99, curve), 100)

    def test_node_key_groups_radios_of_one_unit(self):
        self.assertEqual(analyze.node_key("08:f0:1e:10:20:31"), analyze.node_key("0a:f0:1e:10:20:35"))
        self.assertNotEqual(analyze.node_key("08:f0:1e:10:20:31"), analyze.node_key("08:f0:1e:10:21:42"))
        self.assertIsNone(analyze.node_key(None))


class CoverageRuleTests(unittest.TestCase):
    def test_empty_bundle(self):
        result = analyze.analyze(bundle())
        self.assertIsNone(result["score"])
        self.assertEqual(ids(result), ["no_survey"])
        self.assertEqual(plan_ids(result), ["run_survey"])

    def test_single_router_dead_zone_suggests_mesh(self):
        spots = [spot("Living room", -45, 300, 4, 0, 0, ts=1), spot("Hall", -62, 150, 6, 5, 0, ts=2),
                 spot("Back bedroom", -86, 1.5, 180, 12, 0, ts=3)]
        result = analyze.analyze(bundle(survey={"floors": ["Main floor"], "spots": spots, "collector_via": "ethernet"}))
        self.assertIn("dead_zones", ids(result))
        self.assertEqual(result["findings"][0]["severity"], "critical")
        self.assertEqual(plan_ids(result)[:2], ["reposition_router", "add_mesh"])
        hint = " ".join(result["plan"][1]["notes"])
        self.assertIn("Hall", hint)
        self.assertEqual(result["coverage"]["coverage_pct"], 67)

    def test_mesh_weak_zone_suggests_moving_a_node(self):
        spots = [spot("Kitchen", -50, 200, 5, 0, 0, bssid="08:f0:1e:10:20:31"),
                 spot("Office", -76, 12, 40, 9, 0, bssid="08:f0:1e:10:21:42")]
        result = analyze.analyze(bundle(survey={"floors": ["Main floor"], "spots": spots, "collector_via": "ethernet"}))
        self.assertIn("weak_zones", ids(result))
        self.assertTrue(result["context"]["is_mesh"])
        self.assertEqual(plan_ids(result)[0], "move_node")
        self.assertEqual(len(result["coverage"]["nodes"]), 2)

    def test_outdoor_spots_get_outdoor_advice(self):
        spots = [spot("Kitchen", -50, 200, 5), spot("Back yard", -84, 2, 150)]
        result = analyze.analyze(bundle(survey={"floors": ["Main floor"], "spots": spots, "collector_via": "ethernet"}))
        self.assertIn("outdoor_gap", ids(result))
        self.assertNotIn("dead_zones", ids(result))
        self.assertEqual(plan_ids(result)[0], "outdoor_ap")

    def test_all_good(self):
        spots = [spot("Kitchen", -50, 200, 5), spot("Den", -58, 150, 6)]
        result = analyze.analyze(bundle(survey={"floors": ["Main floor"], "spots": spots, "collector_via": "ethernet"}))
        self.assertIn("coverage_ok", ids(result))
        self.assertGreaterEqual(result["score"], 85)

    def test_floor_gap(self):
        spots = [spot("Kitchen", -48, 250, 4), spot("Den", -55, 200, 5),
                 spot("Bedroom", -74, 15, 30, floor="Upstairs"), spot("Bath", -71, 30, 20, floor="Upstairs")]
        result = analyze.analyze(bundle(survey={"floors": ["Main floor", "Upstairs"], "spots": spots, "collector_via": "ethernet"}))
        self.assertIn("floor_gap", ids(result))

    def test_strong_signal_but_slow(self):
        spots = [spot("Kitchen", -48, 250, 4), spot("Office", -55, 9, 12)]
        result = analyze.analyze(bundle(survey={"floors": ["Main floor"], "spots": spots, "collector_via": "ethernet"}))
        self.assertIn("strong_but_slow", ids(result))

    def test_sticky_roaming(self):
        spots = [spot("Hall", -58, 150, 6, 0, 0, bssid="08:f0:1e:10:20:31"),
                 spot("Stairs", -76, 20, 30, 3, 0, bssid="08:f0:1e:10:21:42")]
        result = analyze.analyze(bundle(survey={"floors": ["Main floor"], "spots": spots, "collector_via": "ethernet"}))
        self.assertIn("sticky_roaming", ids(result))

    def test_slow_band_despite_strong_signal(self):
        spots = [spot("Kitchen", -48, 60, 5, channel=6, band="2.4"), spot("Den", -52, 55, 6, channel=6, band="2.4")]
        result = analyze.analyze(bundle(survey={"floors": ["Main floor"], "spots": spots, "collector_via": "ethernet"}))
        self.assertIn("band_24_strong", ids(result))

    def test_wifi_slower_than_the_line(self):
        spots = [spot("Next to router", -40, 140, 4)]
        result = analyze.analyze(bundle(profile={"plan_down": 1000}, survey={"floors": ["Main floor"], "spots": spots, "collector_via": "ethernet"}))
        self.assertIn("wifi_caps_plan", ids(result))
        self.assertIn("router_upgrade", plan_ids(result))


class OtherRuleTests(unittest.TestCase):
    def test_channel_off_grid(self):
        wifi = {"link": {"connected": True, "band": "2.4", "channel": 4, "width": 20, "security": "WPA2 Personal", "generation": "Wi-Fi 6"},
                "channels": {"density": {"visible": 3, "strong": 1, "level": "low"},
                             "bands": {"2.4": {"current": 4, "recommended": 11, "advice": "move", "current_score": 2.0, "scores": []}}}}
        result = analyze.analyze(bundle(wifi=wifi))
        self.assertIn("channel_offgrid", ids(result))
        self.assertIn("channel 11", " ".join(result["plan"][0]["notes"]))

    def test_dense_building_guesses_apartment(self):
        wifi = {"link": {"connected": False}, "channels": {"density": {"visible": 34, "strong": 9, "level": "very high"}, "bands": {}}}
        result = analyze.analyze(bundle(wifi=wifi))
        self.assertIn("dense_rf", ids(result))
        self.assertEqual(result["context"]["home"], "apartment")
        self.assertTrue(result["context"]["home_guessed"])
        self.assertIn("prefer_5_6ghz", plan_ids(result))

    def test_security_and_old_standard(self):
        wifi = {"link": {"connected": True, "band": "2.4", "channel": 6, "security": "WEP", "generation": "Wi-Fi 4", "phy": "802.11n", "tx_rate": 72},
                "channels": {"bands": {}}}
        result = analyze.analyze(bundle(wifi=wifi))
        self.assertIn("weak_security", ids(result))
        self.assertIn("old_wifi", ids(result))

    def test_high_noise(self):
        wifi = {"link": {"connected": True, "band": "2.4", "channel": 6, "rssi": -58, "noise": -72, "snr": 14, "security": "WPA2 Personal"},
                "channels": {"bands": {}}}
        self.assertIn("high_noise", ids(analyze.analyze(bundle(wifi=wifi))))

    def test_offline_blames_the_right_side(self):
        local = {"online": False, "gateway": {"median": None, "sent": 10}, "internet": None}
        upstream = {"online": False, "gateway": {"median": 2.0, "sent": 10, "loss_pct": 0}, "internet": None}
        self.assertIn("offline_local", ids(analyze.analyze(bundle(latency=local))))
        self.assertIn("offline", ids(analyze.analyze(bundle(latency=upstream))))

    def test_speed_against_plan_by_cable(self):
        result = analyze.analyze(bundle(profile={"plan_down": 500, "plan_up": 20}, speed={"down_mbps": 210, "up_mbps": 19, "errors": []}))
        self.assertIn("below_plan", ids(result))
        self.assertEqual([f["severity"] for f in result["findings"] if f["id"] == "below_plan"], ["major"])
        self.assertIn("isp_ticket", plan_ids(result))

    def test_speed_against_plan_over_wifi_asks_for_a_retest(self):
        result = analyze.analyze(bundle(network={"via": "wifi"}, profile={"plan_down": 500},
                                        speed={"down_mbps": 210, "up_mbps": 19, "errors": []}))
        self.assertIn("below_plan_wifi", ids(result))
        self.assertNotIn("isp_ticket", plan_ids(result))

    def test_bufferbloat_and_slow_upload(self):
        speed_data = {"down_mbps": 300, "up_mbps": 3.5, "bloat_grade": "F", "bloat_ms": 480, "idle_ms": 20,
                      "loaded_down_ms": 90, "loaded_up_ms": 500, "errors": []}
        result = analyze.analyze(bundle(speed=speed_data))
        self.assertIn("bufferbloat", ids(result))
        self.assertIn("slow_upload", ids(result))
        self.assertIn("sqm_qos", plan_ids(result))

    def test_packet_loss_beyond_a_clean_router_hop(self):
        latency = {"online": True, "gateway": {"median": 2.0, "jitter": 0.5, "loss_pct": 0},
                   "internet": {"median": 24.0, "jitter": 4.0, "loss_pct": 6.0}}
        self.assertIn("internet_loss", ids(analyze.analyze(bundle(latency=latency))))

    def test_slow_and_failing_dns(self):
        slow = {"system": [{"label": "Your DNS", "server": "192.168.1.1", "failures": 0, "samples": 6, "avg_ms": 120}],
                "public": [], "best_public": {"label": "Cloudflare", "server": "1.1.1.1", "avg_ms": 18}, "system_avg_ms": 120}
        failing = {"system": [{"label": "Your DNS", "server": "192.168.1.1", "failures": 4, "samples": 6, "avg_ms": 30}],
                   "public": [], "best_public": {"label": "Cloudflare", "server": "1.1.1.1", "avg_ms": 18}, "system_avg_ms": 30}
        self.assertIn("dns_slow", ids(analyze.analyze(bundle(dns=slow))))
        self.assertIn("dns_failing", ids(analyze.analyze(bundle(dns=failing))))

    def test_modem_levels(self):
        bad = {"status": "ok", "kind": "cable", "summary": {"downstream_channels": 24, "upstream_channels": 4, "down_power_min": -16.5,
                                                              "down_power_max": 3.0, "snr_min": 29.0, "up_power_max": 52.0, "up_power_min": 50.0,
                                                              "uncorrected": 91000, "docsis": "3.0"}}
        result = analyze.analyze(bundle(modem=bad, profile={"plan_down": 800}))
        finding = [f for f in result["findings"] if f["id"] == "modem_signal"][0]
        self.assertEqual(finding["severity"], "major")
        self.assertEqual(len(finding["evidence"]), 4)
        self.assertIn("modem_old", ids(result))
        good = {"status": "ok", "kind": "cable", "summary": {"downstream_channels": 32, "upstream_channels": 4, "down_power_min": -2.0,
                                                               "down_power_max": 4.0, "snr_min": 39.0, "up_power_max": 44.0, "up_power_min": 42.0,
                                                               "uncorrected": 3, "docsis": "3.1"}}
        self.assertIn("modem_ok", ids(analyze.analyze(bundle(modem=good))))

    def test_cellular_gateway_signal(self):
        weak = {"status": "ok", "kind": "cellular", "cellular": {"5g": {"rsrp": -109, "sinr": 1, "bands": ["n71"]}}}
        result = analyze.analyze(bundle(modem=weak))
        self.assertIn("cell_signal_poor", ids(result))
        self.assertEqual(plan_ids(result)[0], "gateway_window")

    def test_double_nat_and_extenders(self):
        devices_found = [{"ip": "192.168.1.1", "gateway": True, "infra": True, "kind": "router", "brand": "NETGEAR", "label": "NETGEAR router", "name": "router"},
                         {"ip": "192.168.1.9", "gateway": False, "infra": True, "kind": "extender", "brand": "TP-Link", "label": "extender", "name": "RE650"}]
        result = analyze.analyze(bundle(scan={"devices": devices_found}, router={"double_nat": True, "evidence": ["x"], "extra_routers": []}))
        self.assertIn("double_nat", ids(result))
        self.assertIn("extenders", ids(result))
        self.assertIn("mixed_brands", ids(result))
        self.assertEqual(plan_ids(result)[0], "bridge_mode")

    def test_shaky_fixed_devices(self):
        devices_found = [{"ip": "1", "kind": "camera", "name": "Garage cam", "loss_pct": 50, "rtt_ms": 200},
                         {"ip": "2", "kind": "tv", "name": "Bedroom TV", "loss_pct": 25, "rtt_ms": 80},
                         {"ip": "3", "kind": "phone", "name": "Phone", "loss_pct": 75, "rtt_ms": 300}]
        result = analyze.analyze(bundle(scan={"devices": devices_found}))
        finding = [f for f in result["findings"] if f["id"] == "unstable_devices"][0]
        self.assertEqual(finding["where"], ["Garage cam", "Bedroom TV"])   # phones sleep: not counted

    def test_eero_rules(self):
        data = {"network": {"sqm": False, "update_available": True},
                "nodes": [{"name": "Gateway", "gateway": True, "wired": True, "status": "green", "mesh_bars": 5, "clients": 8},
                          {"name": "Office", "gateway": False, "wired": False, "status": "green", "mesh_bars": 1, "clients": 4},
                          {"name": "Attic", "gateway": False, "wired": False, "status": "red", "mesh_bars": 0, "clients": 0}],
                "clients": [{"name": "Doorbell", "connected": True, "wireless": True, "rssi": -82, "node": "Gateway", "band": "2.4"},
                            {"name": "TV", "connected": True, "wireless": True, "rssi": -50, "node": "Gateway", "band": "5"}]}
        result = analyze.analyze(bundle(eero=data))
        for expected in ("eero_offline", "eero_weak_link", "eero_weak_clients", "eero_update"):
            self.assertIn(expected, ids(result))
        self.assertTrue(result["context"]["is_eero"])
        self.assertEqual(result["findings"][0]["id"], "eero_offline")

    def test_monitor_rules(self):
        now = time.time()
        samples = [{"t": now + i * 10, "gw_ms": 3.0, "gw_loss": 0.0, "net_ms": 20.0, "net_loss": 0.0} for i in range(400)]
        for index in range(100, 112):
            samples[index].update({"net_ms": None, "net_loss": 100.0})
        for index in range(300, 304):
            samples[index].update({"net_ms": None, "net_loss": 100.0, "gw_ms": None, "gw_loss": 100.0})
        summary = monitor.summarize(samples)
        self.assertEqual((summary["upstream_outages"], summary["local_outages"]), (1, 1))
        self.assertEqual(round(summary["outages"][0]["seconds"]), 120)
        self.assertEqual(summary["uptime_pct"], 96.0)
        result = analyze.analyze(bundle(monitor=summary))
        self.assertIn("isp_outages", ids(result))
        self.assertIn("local_outages", ids(result))

    def test_monitor_stable(self):
        now = time.time()
        samples = [{"t": now + i * 10, "gw_ms": 3.0, "gw_loss": 0.0, "net_ms": 20.0, "net_loss": 0.0} for i in range(400)]
        result = analyze.analyze(bundle(monitor=monitor.summarize(samples)))
        self.assertIn("stable", ids(result))

    def test_state_of(self):
        self.assertEqual(monitor.state_of({"net_ms": 20}), "ok")
        self.assertEqual(monitor.state_of({"net_ms": None, "gw_ms": None, "gw_loss": 100}), "local")
        self.assertEqual(monitor.state_of({"net_ms": None, "gw_ms": 3, "gw_loss": 0}), "upstream")


class PlanTests(unittest.TestCase):
    def test_every_fix_referenced_by_rules_exists(self):
        import re
        import inspect
        source = inspect.getsource(analyze)
        referenced = set(re.findall(r'"([a-z_0-9]+)"', source)) & set(solutions.CATALOG) | {"run_survey"}
        for fix in referenced:
            entry = solutions.CATALOG[fix]
            self.assertTrue(entry["title"] and entry["why"] and entry["steps"], fix)
            self.assertIn(entry["cost"], solutions.COST_ORDER, fix)

    def test_demo_bundle(self):
        audit, survey, eero_data, samples = demo.build()
        data = dict(audit)
        data.update({"profile": survey["profile"], "eero": eero_data, "monitor": monitor.summarize(samples),
                     "survey": {"floors": survey["floors"], "spots": survey["spots"], "collector_via": "wifi"}})
        result = json.loads(json.dumps(analyze.analyze(data)))
        self.assertEqual(result["findings"][0]["id"], "dead_zones")
        self.assertTrue(30 <= result["score"] <= 70)
        self.assertEqual(result["plan"][0]["id"], "move_node")
        self.assertEqual([p["step"] for p in result["plan"]], list(range(1, len(result["plan"]) + 1)))
        self.assertEqual(len(result["spots"]), len(survey["spots"]))
        self.assertTrue(all(f["severity"] in analyze.SEVERITY_RANK for f in result["findings"]))


class SpeedTests(unittest.TestCase):
    def test_network_quality(self):
        text = json.dumps({"base_rtt": 31.5, "dl_flows": 12, "dl_throughput": 404216096, "responsiveness": 1113,
                           "ul_flows": 12, "ul_throughput": 22338840, "interface_name": "en0"})
        result = speed.parse_network_quality("some banner\n" + text)
        self.assertEqual((result["down_mbps"], result["up_mbps"], result["rpm"]), (404.22, 22.34, 1113))
        self.assertEqual(result["bloat_grade"], "A")
        self.assertIsNone(speed.parse_network_quality("error: no network"))
        self.assertIsNone(speed.parse_network_quality("{}"))

    def test_bufferbloat_grades(self):
        self.assertEqual([speed.bufferbloat_grade(v) for v in (2, 20, 45, 150, 300, 900)], ["A+", "A", "B", "C", "D", "F"])
        self.assertIsNone(speed.bufferbloat_grade(None))

    def test_meta_formats(self):
        as_json = speed.parse_meta('{"clientIp":"203.0.113.7","asn":7922,"asOrganization":"Comcast Cable","colo":"EWR","country":"US"}')
        self.assertEqual((as_json["isp"], as_json["colo"], as_json["ip"]), ("Comcast Cable", "EWR", "203.0.113.7"))
        nested = speed.parse_meta('{"clientIp":"203.0.113.7","colo":{"iata":"ORD","city":"Chicago"}}')
        self.assertEqual(nested["colo"], "ORD")
        trace = speed.parse_meta("fl=1f\nh=speed.cloudflare.com\nip=203.0.113.7\ncolo=EWR\nloc=US\n")
        self.assertEqual((trace["ip"], trace["colo"], trace["country"]), ("203.0.113.7", "EWR", "US"))
        self.assertIsNone(speed.parse_meta("")["ip"])

    def test_mask_ip(self):
        self.assertEqual(speed.mask_ip("203.0.113.7"), "203.x.x.7")
        self.assertIsNone(speed.mask_ip(None))


class EeroTests(unittest.TestCase):
    def test_normalize(self):
        network = {"name": "Home", "health": {"internet": {"status": "connected", "isp_up": True}}, "connection": {"mode": "bridge"},
                   "speed": {"down": {"value": 420.5, "units": "Mbps"}, "up": {"value": 21.0}}, "sqm": False,
                   "updates": {"has_update": True}}
        nodes = [{"location": "Living room", "model": "eero Pro 6E", "gateway": True, "wired": True, "status": "green",
                  "mesh_quality_bars": 5, "connected_clients_count": 9, "ip_address": "192.168.4.1", "os_version": "7.4.1"}, "junk"]
        clients = [{"nickname": "Doorbell", "mac": "AA:BB:CC:DD:EE:FF", "connected": True, "wireless": True,
                    "connectivity": {"signal": "-71 dBm", "score_bars": 2, "rx_rate_info": {"rate_bps": 65000000}},
                    "interface": {"frequency": "2.4", "frequency_unit": "GHz"}, "source": {"location": "Living room"}},
                   {"hostname": "nas", "connected": True, "wireless": False, "connectivity": {}}]
        result = eero.normalize(network, nodes, clients)
        self.assertTrue(result["network"]["bridge"])
        self.assertEqual((result["network"]["speed_down"], result["network"]["update_available"]), (420.5, True))
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["nodes"][0]["mesh_bars"], 5)
        doorbell, nas = result["clients"]
        self.assertEqual((doorbell["rssi"], doorbell["band"], doorbell["node"], doorbell["rx_mbps"], doorbell["mac"]),
                         (-71, "2.4", "Living room", 65.0, "aa:bb:cc:dd:ee:ff"))
        self.assertEqual((nas["name"], nas["rssi"], nas["band"]), ("nas", None, None))

    def test_normalize_tolerates_missing_everything(self):
        result = eero.normalize({}, None, None)
        self.assertEqual((result["nodes"], result["clients"]), ([], []))


if __name__ == "__main__":
    unittest.main()
