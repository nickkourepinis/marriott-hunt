import json
import os
import shutil
import socket
import ssl
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from wifiscout import cli, demo, monitor, qr, report, server, speed, store, tlscert

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class QrTests(unittest.TestCase):
    # Module rows for "https://192.168.4.23:8788/m?k=ab12cd34", version 3, mask 2. Checked against the
    # qrcode reference library and decoded with zxing when this test was written.
    GOLDEN = [
        "11111110001110100001001111111", "10000010000011010100001000001", "10111010110100111000001011101",
        "10111010101001010111101011101", "10111010111010101101001011101", "10000010111100111010001000001",
        "11111110101010101010101111111", "00000000110110000011000000000", "10111110000000010111101111100",
        "11011101000111001101001010001", "01010110010011110010110000000", "11010101001011000011000101010",
        "00101011111100111110110001100", "01010001000100000111001010001", "10011111001010011100000001100",
        "00101001111010110001011010010", "11010011000010011100110101100", "11011100100001100111001010101",
        "10111110101111110000011110100", "10000001101111000001110000010", "10010110001010110111111110111",
        "00000000111010001100100011111", "11111110001000011101101011100", "10000010100110110000100010011",
        "10111010100000010100111110100", "10111010110101101110010101111", "10111010110100011111011011110",
        "10000010000001011001101110010", "11111110100001001110011011100",
    ]

    def test_matches_verified_symbol(self):
        matrix = qr.encode("https://192.168.4.23:8788/m?k=ab12cd34", version=3, mask=2)
        self.assertEqual(["".join("1" if cell else "0" for cell in row) for row in matrix], self.GOLDEN)

    def test_structure(self):
        matrix = qr.encode("https://192.168.4.23:8788/m?k=ab12cd34")
        size = len(matrix)
        self.assertEqual(size, 29)
        self.assertTrue(all(len(row) == size for row in matrix))
        finder = ["1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111"]
        for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
            block = ["".join("1" if matrix[top + r][left + c] else "0" for c in range(7)) for r in range(7)]
            self.assertEqual(block, finder)
        self.assertTrue(matrix[size - 8][8])                     # the always-dark module
        timing = "".join("1" if matrix[6][c] else "0" for c in range(8, size - 8))
        self.assertEqual(timing, ("10" * size)[:len(timing)])

    def test_versions_grow_with_length(self):
        sizes = [len(qr.encode("x" * n)) for n in (5, 20, 40, 100, 200)]
        self.assertEqual(sizes, sorted(sizes))
        self.assertEqual(len(qr.encode("x" * 200)), 57)
        with self.assertRaises(ValueError):
            qr.encode("x" * 400)

    def test_renderers(self):
        matrix = qr.encode("hello")
        svg = qr.to_svg(matrix)
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
        self.assertNotIn("<script", svg)
        text = qr.to_terminal(matrix)
        self.assertEqual(len(text.splitlines()), (len(matrix) + 4 + 1) // 2)

    def test_reed_solomon_known_vector(self):
        # ISO 18004 worked example: "01234567" as a 1-M symbol has these error correction words.
        data = [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11]
        self.assertEqual(qr._rs_remainder(data, qr._rs_divisor(10)), [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55])


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def handshake(self, cert, key):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def serve():
            conn, _ = listener.accept()
            with context.wrap_socket(conn, server_side=True) as secure:
                secure.recv(16)
                secure.sendall(b"pong")

        thread = threading.Thread(target=serve)
        thread.start()
        client = ssl.create_default_context(cafile=cert)
        with client.wrap_socket(socket.create_connection(("127.0.0.1", listener.getsockname()[1])), server_hostname="127.0.0.1") as conn:
            conn.sendall(b"ping")
            reply = conn.recv(16)
        thread.join()
        listener.close()
        return reply

    def test_pure_python_certificate_is_accepted_by_tls(self):
        made = tlscert.ensure(self.folder, ["127.0.0.1", "192.168.4.23", "laptop.local"], prefer_openssl=False)
        self.assertIsNotNone(made)
        self.assertEqual(self.handshake(*made), b"pong")
        self.assertEqual(len(tlscert.fingerprint(made[0]).split(":")), 32)
        if os.name == "posix":
            self.assertEqual(os.stat(made[1]).st_mode & 0o077, 0)

    def test_certificate_is_reused_until_hosts_change(self):
        first = tlscert.ensure(self.folder, ["127.0.0.1"], prefer_openssl=False)
        stamp = os.path.getmtime(first[0])
        tlscert.ensure(self.folder, ["127.0.0.1"], prefer_openssl=False)
        self.assertEqual(os.path.getmtime(first[0]), stamp)
        before = tlscert.fingerprint(first[0])
        tlscert.ensure(self.folder, ["127.0.0.1", "10.0.0.9"], prefer_openssl=False)
        self.assertNotEqual(tlscert.fingerprint(first[0]), before)

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_openssl_certificate(self):
        made = tlscert.ensure(self.folder, ["127.0.0.1", "localhost"])
        self.assertEqual(self.handshake(*made), b"pong")

    def test_rsa_primitives(self):
        self.assertTrue(tlscert._is_probable_prime(2 ** 127 - 1))
        self.assertFalse(tlscert._is_probable_prime(2 ** 127 + 1))
        self.assertEqual(tlscert._inverse(65537, 3120 * 77) * 65537 % (3120 * 77), 1)
        self.assertIsNone(tlscert._inverse(6, 9))


class SurveyStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.survey = store.Survey(store.DataDir(self.folder))

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_spot_fields_are_cleaned(self):
        spot = self.survey.upsert_spot({"name": "  Kit\x00chen<script> ", "x": "3.5", "y": None, "down_mbps": "fast", "rssi": 61,
                                        "rtt_ms": 4.26, "evil": "x" * 9999, "bssid": "A4-11-62-0-B-C"}, "10.0.0.5")
        self.assertEqual(spot["name"], "Kit chen<script>")
        self.assertEqual((spot["x"], spot["y"], spot["down_mbps"], spot["rtt_ms"]), (3.5, None, None, 4.26))
        self.assertIsNone(spot["rssi"])            # positive dBm is not a signal reading
        self.assertEqual(spot["bssid"], "a4:11:62:00:0b:0c")
        self.assertNotIn("evil", spot)
        self.assertEqual(spot["floor"], "Main floor")

    def test_update_keeps_other_fields(self):
        spot = self.survey.upsert_spot({"name": "Den", "down_mbps": 80, "x": 1, "y": 2}, "10.0.0.5")
        again = self.survey.upsert_spot({"id": spot["id"], "rssi": -64}, "10.0.0.5")
        self.assertEqual((again["name"], again["down_mbps"], again["rssi"], again["x"]), ("Den", 80, -64, 1))
        self.assertEqual(len(self.survey.snapshot()["spots"]), 1)

    def test_shortcut_reading_attaches_to_latest_spot_from_same_phone(self):
        self.survey.upsert_spot({"name": "Den", "down_mbps": 80}, "10.0.0.5")
        self.survey.upsert_spot({"name": "Other phone", "down_mbps": 80}, "10.0.0.6")
        spot, outcome = self.survey.add_radio({"rssi": "-61", "ch": "44", "std": "802.11ax"}, "10.0.0.5")
        self.assertEqual((outcome, spot["name"], spot["rssi"], spot["channel"], spot["band"]), ("attached", "Den", -61, 44, "5"))
        other = [s for s in self.survey.snapshot()["spots"] if s["name"] == "Other phone"][0]
        self.assertIsNone(other.get("rssi"))

    def test_shortcut_reading_waits_for_next_measurement(self):
        _, outcome = self.survey.add_radio({"rssi": "-70"}, "10.0.0.5")
        self.assertEqual(outcome, "waiting")
        spot = self.survey.upsert_spot({"name": "Hall", "down_mbps": 50}, "10.0.0.5")
        self.assertEqual(spot["rssi"], -70)

    def test_shortcut_can_send_values_in_separate_requests(self):
        self.survey.upsert_spot({"name": "Den", "down_mbps": 80}, "10.0.0.5")
        self.assertEqual(self.survey.add_radio({"ch": "6"}, "10.0.0.5")[1], "partial")
        self.assertEqual(self.survey.add_radio({"rssi": "-58"}, "10.0.0.5")[1], "attached")
        spot, outcome = self.survey.add_radio({"bssid": "08:f0:1e:10:20:31"}, "10.0.0.5")
        self.assertEqual((outcome, spot["rssi"], spot["channel"], spot["bssid"]), ("attached", -58, 6, "08:f0:1e:10:20:31"))

    def test_named_shortcut_reading_becomes_a_spot(self):
        spot, outcome = self.survey.add_radio({"rssi": "-80"}, "10.0.0.5", name="Porch")
        self.assertEqual((outcome, spot["name"]), ("new spot", "Porch"))
        self.assertEqual(self.survey.add_radio({}, "10.0.0.5"), (None, "no values"))

    def test_profile_and_floors(self):
        profile = self.survey.set_profile({"home": "castle", "sqft": "2400", "plan_down": "500", "plan_up": -3})
        self.assertEqual(profile, {"sqft": 2400, "plan_down": 500})
        self.survey.upsert_spot({"name": "Loft", "floor": "Attic", "rssi": -60}, "x")
        self.assertEqual(self.survey.set_floors(["Main floor", "Upstairs", "Upstairs", ""]), ["Main floor", "Upstairs", "Attic"])

    def test_floor_plan_validation(self):
        good = "data:image/png;base64,iVBORw0KGgo="
        self.assertTrue(self.survey.set_plan("Main floor", good, 14))
        self.assertEqual(self.survey.snapshot()["plans"]["Main floor"]["width_m"], 14)
        self.assertFalse(self.survey.set_plan("Main floor", "javascript:alert(1)", 10))
        self.assertFalse(self.survey.set_plan("Main floor", "data:image/svg+xml;base64,PHN2Zz4=", 10))
        self.assertFalse(self.survey.set_plan("Main floor", "data:image/png;base64," + "A" * (store.MAX_PLAN_BYTES + 1), 10))
        self.assertTrue(self.survey.set_plan("Main floor", None, None))
        self.assertEqual(self.survey.snapshot()["plans"], {})

    def test_persists_to_disk(self):
        self.survey.upsert_spot({"name": "Den", "rssi": -60}, "x")
        reloaded = store.Survey(store.DataDir(self.folder))
        self.assertEqual(reloaded.snapshot()["spots"][0]["name"], "Den")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.data = store.DataDir(cls.folder)
        cls.app, cls.servers = server.start(cls.data, local_ip="127.0.0.1", collector_via="ethernet",
                                            http_port=free_port(), https_port=free_port(), token="unit-test-key")
        cls.base = "http://127.0.0.1:%d" % cls.app.http_port

    @classmethod
    def tearDownClass(cls):
        for item in cls.servers:
            item.shutdown()
            item.server_close()
        shutil.rmtree(cls.folder, ignore_errors=True)

    def call(self, path, body=None, key="unit-test-key", method=None):
        headers = {"X-WS-Key": key} if key else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with OPENER.open(request, timeout=10) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def test_key_is_required(self):
        self.assertEqual(self.call("/api/state", key=None)[0], 403)
        self.assertEqual(self.call("/api/state", key="wrong")[0], 403)
        self.assertEqual(self.call("/", key=None)[0], 403)
        self.assertEqual(self.call("/api/spot", body={"name": "x"}, key=None)[0], 403)
        self.assertEqual(self.call("/api/state?k=unit-test-key", key=None)[0], 200)

    def test_phone_page_and_report_load(self):
        status, body, headers = self.call("/m", key=None)
        self.assertEqual(status, 200)
        self.assertIn(b"WiFi Scout", body)
        self.assertNotIn(b"/*INLINE:", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, body, _ = self.call("/?k=unit-test-key", key=None)
        self.assertEqual(status, 200)
        self.assertIn(b"ws-data", body)
        self.assertIn(b"\\u003csvg", body)   # the QR code for the phone, escaped inside the data block

    def test_unknown_paths(self):
        self.assertEqual(self.call("/etc/passwd")[0], 404)
        self.assertEqual(self.call("/api/nothing")[0], 404)
        self.assertEqual(self.call("/static/../../secret")[0], 404)

    def test_spot_round_trip_and_analysis(self):
        status, body, _ = self.call("/api/spot", body={"name": "Kitchen", "x": 1, "y": 2, "rssi": -52, "down_mbps": 220, "rtt_ms": 5})
        saved = json.loads(body)["spot"]
        self.assertEqual((status, saved["scores"]["grade"]), (200, "strong"))
        self.assertNotIn("client", saved)
        state = json.loads(self.call("/api/state")[1])
        self.assertIn("Kitchen", [s["name"] for s in state["spots"]])
        self.assertIn("/api/radio?k=unit-test-key", state["server"]["radio_url"])
        analysis = json.loads(self.call("/api/analysis")[1])
        self.assertIn("coverage_ok", [f["id"] for f in analysis["findings"]])
        self.assertTrue(json.loads(self.call("/api/spot/delete", body={"id": saved["id"]})[1])["deleted"])

    def test_bad_requests(self):
        request = urllib.request.Request(self.base + "/api/spot", data=b"{not json", headers={"X-WS-Key": "unit-test-key"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            OPENER.open(request, timeout=10)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.call("/api/plan", body={"floor": "Main floor", "image": "http://evil/x.png"})[0], 400)
        self.assertEqual(self.call("/api/radio?k=unit-test-key", key=None)[0], 400)

    def test_shortcut_endpoint(self):
        self.call("/api/spot", body={"name": "Hall", "down_mbps": 90})
        status, body, _ = self.call("/api/radio?k=unit-test-key&rssi=-63&ch=149&tx=780", key=None)
        self.assertEqual(status, 200)
        self.assertIn(b"-63 dBm saved to Hall", body)

    def test_speed_endpoints_with_the_speed_client(self):
        status, body, headers = self.call("/api/down?bytes=300000")
        self.assertEqual((status, len(body), headers["Content-Length"]), (200, 300000, "300000"))
        request = urllib.request.Request(self.base + "/api/up", data=b"x" * 200000, headers={"X-WS-Key": "unit-test-key"})
        with OPENER.open(request, timeout=10) as response:
            self.assertEqual(json.loads(response.read())["bytes"], 200000)
        endpoint = speed.Endpoint("local", "http", "127.0.0.1", self.app.http_port,
                                  "/api/down?k=unit-test-key&bytes=%d", "/api/up?k=unit-test-key")
        result = speed.http_speedtest(endpoint=endpoint, duration=1.2, streams=2, anchor=None)
        self.assertEqual(result["errors"], [])
        self.assertGreater(result["down_mbps"], 20)
        self.assertGreater(result["up_mbps"], 20)

    def test_speed_client_reports_failures(self):
        dead = speed.Endpoint("dead", "http", "127.0.0.1", free_port(), "/down?bytes=%d", "/up")
        result = speed.http_speedtest(endpoint=dead, duration=0.6, streams=2, anchor=None)
        self.assertIsNone(result["down_mbps"])
        self.assertEqual(len(result["errors"]), 2)

    def test_https_listener_with_generated_certificate(self):
        self.assertIsNotNone(self.app.https_port)
        context = ssl.create_default_context(cafile=self.data.path(os.path.join("tls", "cert.pem")))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
        request = urllib.request.Request("https://127.0.0.1:%d/api/ping" % self.app.https_port, headers={"X-WS-Key": "unit-test-key"})
        with opener.open(request, timeout=10) as response:
            self.assertIn("t", json.loads(response.read()))
        self.assertTrue(self.app.phone_url().startswith("https://127.0.0.1:"))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.data = store.DataDir(self.folder)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_demo_report_is_self_contained(self):
        demo.write(self.data)
        path, bundle = report.write(self.data)
        with open(path, encoding="utf-8") as handle:
            html = handle.read()
        self.assertNotIn("/*INLINE:", html)
        self.assertNotIn("/*DATA*/", html)
        self.assertNotIn("http://", html.split('id="ws-data"')[0].replace("http://www.w3.org", ""))
        self.assertIn("Maple House", html)
        self.assertEqual(bundle["analysis"]["findings"][0]["id"], "dead_zones")
        self.assertTrue(bundle["monitor"]["samples"] > 1000)
        self.assertLessEqual(len(bundle["monitor_series"]), 500)

    def test_hostile_names_cannot_break_out_of_the_data_block(self):
        audit, survey, _eero, _samples = demo.build()
        audit["wifi"]["neighbors"][3]["ssid"] = "</script><script>alert(1)</script>"
        survey["spots"][0]["name"] = "<img src=x onerror=alert(1)>"
        self.data.save_json("audit.json", audit)
        self.data.save_json("survey.json", survey)
        path, _ = report.write(self.data)
        with open(path, encoding="utf-8") as handle:
            html = handle.read()
        block = html.split('id="ws-data" type="application/json">')[1].split("</script>")[0]
        self.assertNotIn("<", block)
        self.assertIn("alert(1)", json.loads(block)["bundle"]["wifi"]["neighbors"][3]["ssid"])

    def test_empty_folder_still_renders(self):
        path, bundle = report.write(self.data)
        self.assertIsNone(bundle["analysis"]["score"])
        self.assertTrue(os.path.getsize(path) > 10000)

    def test_monitor_log_round_trip(self):
        _, _, _, samples = demo.build()
        with open(self.data.path("monitor.jsonl"), "w", encoding="utf-8") as handle:
            for sample in samples[:50]:
                handle.write(json.dumps(sample) + "\n")
            handle.write("garbage line\n")
        self.assertEqual(len(monitor.load_samples(self.data.path("monitor.jsonl"))), 50)


class CliTests(unittest.TestCase):
    def test_parser_defaults_to_run(self):
        parser = cli.build_parser()
        args = parser.parse_args(["run", "--plan", "500/20", "--home", "apartment", "--no-survey"])
        self.assertEqual((args.plan, args.home, args.no_survey, args.port), ("500/20", "apartment", True, 8787))
        self.assertEqual(parser.parse_args(["monitor", "--hours", "2"]).hours, 2.0)

    def test_profile_from_flags(self):
        folder = tempfile.mkdtemp()
        try:
            data = store.DataDir(folder)
            args = cli.build_parser().parse_args(["report", "--plan", "940/35", "--sqft", "1100", "--home", "condo"])
            cli.apply_profile(data, args)
            self.assertEqual(store.Survey(data).snapshot()["profile"], {"home": "condo", "sqft": 1100, "plan_down": 940, "plan_up": 35})
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
