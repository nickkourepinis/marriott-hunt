import struct
import unittest

from wifiscout import devices, discovery, dnsmsg, modem, oui, router, scan


class PacketBuilder(object):
    """Builds an mDNS response with name compression, like real responders send."""

    def __init__(self):
        self.buf = bytearray(12)
        self.offsets = {}
        self.count = 0

    def name(self, text):
        labels = text.split(".")
        for index in range(len(labels)):
            suffix = ".".join(labels[index:]).lower()
            if suffix in self.offsets:
                self.buf += struct.pack("!H", 0xC000 | self.offsets[suffix])
                return
            self.offsets[suffix] = len(self.buf)
            raw = labels[index].encode("utf-8")
            self.buf += bytes([len(raw)]) + raw
        self.buf += b"\x00"

    def record(self, name, rtype, write_rdata):
        self.name(name)
        self.buf += struct.pack("!HHI", rtype, 0x8001, 120)
        length_at = len(self.buf)
        self.buf += b"\x00\x00"
        write_rdata()
        struct.pack_into("!H", self.buf, length_at, len(self.buf) - length_at - 2)
        self.count += 1

    def ptr(self, name, target):
        self.record(name, dnsmsg.TYPE_PTR, lambda: self.name(target))

    def packet(self):
        struct.pack_into("!HHHHHH", self.buf, 0, 0, 0x8400, 0, self.count, 0, 0)
        return bytes(self.buf)


def sample_response():
    builder = PacketBuilder()
    builder.ptr("_airplay._tcp.local", "Living Room._airplay._tcp.local")

    def srv():
        builder.buf.extend(struct.pack("!HHH", 0, 0, 7000))
        builder.name("Living-Room.local")

    builder.record("Living Room._airplay._tcp.local", dnsmsg.TYPE_SRV, srv)
    txt = b"".join(bytes([len(item)]) + item for item in (b"model=AppleTV11,1", b"deviceid=AA:BB:CC:DD:EE:FF", b"flagonly"))
    builder.record("Living Room._airplay._tcp.local", dnsmsg.TYPE_TXT, lambda: builder.buf.extend(txt))
    builder.record("Living-Room.local", dnsmsg.TYPE_A, lambda: builder.buf.extend(bytes([192, 168, 4, 30])))
    return builder.packet()


class DnsMessageTests(unittest.TestCase):
    def test_query_round_trip(self):
        packet = dnsmsg.build_query([("www.example.com", dnsmsg.TYPE_A)], qid=0x1234)
        parsed = dnsmsg.parse_message(packet)
        self.assertEqual(parsed["id"], 0x1234)
        self.assertFalse(parsed["response"])
        self.assertEqual(parsed["questions"], [{"name": "www.example.com", "type": dnsmsg.TYPE_A}])

    def test_unicast_bit(self):
        packet = dnsmsg.build_query([("_ipp._tcp.local", dnsmsg.TYPE_PTR)], unicast_response=True, recursion=False)
        self.assertEqual(packet[-2:], b"\x80\x01")

    def test_compressed_response(self):
        parsed = dnsmsg.parse_message(sample_response())
        self.assertTrue(parsed["response"])
        kinds = [(r["type"], r["name"]) for r in parsed["records"]]
        self.assertIn((dnsmsg.TYPE_PTR, "_airplay._tcp.local"), kinds)
        ptr = parsed["records"][0]
        self.assertEqual(ptr["target"], "Living Room._airplay._tcp.local")
        srv = [r for r in parsed["records"] if r["type"] == dnsmsg.TYPE_SRV][0]
        self.assertEqual((srv["port"], srv["target"]), (7000, "Living-Room.local"))
        txt = [r for r in parsed["records"] if r["type"] == dnsmsg.TYPE_TXT][0]
        self.assertEqual(txt["txt"]["model"], "AppleTV11,1")
        self.assertEqual(txt["txt"]["flagonly"], "")
        address = [r for r in parsed["records"] if r["type"] == dnsmsg.TYPE_A][0]
        self.assertEqual(address["address"], "192.168.4.30")

    def test_rejects_short_and_looping_packets(self):
        with self.assertRaises(ValueError):
            dnsmsg.parse_message(b"\x00\x01")
        loop = struct.pack("!HHHHHH", 0, 0x8400, 1, 0, 0, 0) + b"\xc0\x0c" + b"\x00\x01\x00\x01"
        with self.assertRaises(ValueError):
            dnsmsg.parse_message(loop)

    def test_truncated_record_does_not_crash(self):
        packet = sample_response()[:-3]
        parsed = dnsmsg.parse_message(packet)
        self.assertGreaterEqual(len(parsed["records"]), 3)


class MdnsTests(unittest.TestCase):
    def test_absorb_maps_service_to_device(self):
        result = discovery.MdnsResult()
        state = {"types": set(discovery.SERVICE_TYPES), "asked": set()}
        follow = discovery.absorb_mdns(result, "192.168.4.30", dnsmsg.parse_message(sample_response()), state)
        entry = result.by_ip["192.168.4.30"]
        self.assertEqual(entry["services"], ["_airplay._tcp"])
        self.assertEqual(entry["names"], ["Living Room"])
        self.assertEqual(entry["txt"]["model"], "AppleTV11,1")
        self.assertEqual(entry["hostname"], "living-room.local")
        self.assertTrue(any(q[1] == dnsmsg.TYPE_SRV for q in follow))

    def test_new_service_types_are_followed_up(self):
        builder = PacketBuilder()
        builder.ptr("_services._dns-sd._udp.local", "_weirdthing._tcp.local")
        result = discovery.MdnsResult()
        state = {"types": set(), "asked": set()}
        follow = discovery.absorb_mdns(result, "192.168.4.9", dnsmsg.parse_message(builder.packet()), state)
        self.assertEqual(follow, [("_weirdthing._tcp.local", dnsmsg.TYPE_PTR)])


SSDP_REPLY = ("HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=120\r\n"
              "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
              "USN: uuid:1234::urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\nEXT:\r\n"
              "SERVER: Linux/5.4 UPnP/1.1 MiniUPnPd/2.2.1\r\nLOCATION: http://192.168.1.1:5000/rootDesc.xml\r\n\r\n")

IGD_XML = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
 <specVersion><major>1</major><minor>0</minor></specVersion>
 <device>
  <deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>
  <friendlyName>RT-AX88U</friendlyName><manufacturer>ASUSTeK Computer Inc.</manufacturer>
  <modelName>RT-AX88U</modelName><modelNumber>386.12</modelNumber>
  <deviceList><device>
   <deviceType>urn:schemas-upnp-org:device:WANDevice:1</deviceType>
   <serviceList><service>
    <serviceType>urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1</serviceType>
    <controlURL>/ctl/CmnIfCfg</controlURL></service></serviceList>
   <deviceList><device>
    <deviceType>urn:schemas-upnp-org:device:WANConnectionDevice:1</deviceType>
    <serviceList><service>
     <serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
     <controlURL>/ctl/IPConn</controlURL></service></serviceList>
   </device></deviceList>
  </device></deviceList>
 </device>
</root>"""

SOAP_REPLY = """<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
<u:GetExternalIPAddressResponse xmlns:u="urn:schemas-upnp-org:service:WANIPConnection:1">
<NewExternalIPAddress>192.168.0.14</NewExternalIPAddress></u:GetExternalIPAddressResponse></s:Body></s:Envelope>"""


class SsdpTests(unittest.TestCase):
    def test_headers(self):
        headers = discovery.parse_ssdp_response(SSDP_REPLY)
        self.assertEqual(headers["location"], "http://192.168.1.1:5000/rootDesc.xml")
        self.assertIn("MiniUPnPd", headers["server"])

    def test_device_description(self):
        info = discovery.parse_device_description(IGD_XML, "http://192.168.1.1:5000/rootDesc.xml")
        self.assertEqual((info["friendly_name"], info["manufacturer"], info["model"]), ("RT-AX88U", "ASUSTeK Computer Inc.", "RT-AX88U"))
        self.assertIn("urn:schemas-upnp-org:device:InternetGatewayDevice:1", info["device_types"])
        controls = {s["type"].split(":")[-2]: s["control"] for s in info["services"]}
        self.assertEqual(controls["WANIPConnection"], "http://192.168.1.1:5000/ctl/IPConn")

    def test_refuses_entity_tricks_and_garbage(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><root>&lol;</root>'
        self.assertEqual(discovery.parse_device_description(bomb)["device_types"], [])
        self.assertEqual(discovery.parse_device_description("<root><unclosed>")["services"], [])

    def test_soap(self):
        self.assertEqual(router.parse_soap(SOAP_REPLY), {"NewExternalIPAddress": "192.168.0.14"})


class RouterTests(unittest.TestCase):
    def test_traceroute_formats(self):
        mac = "traceroute to 1.1.1.1 (1.1.1.1), 4 hops max, 52 byte packets\n 1  192.168.4.1  2.345 ms\n 2  192.168.1.254  4.1 ms\n 3  *\n 4  12.83.1.1  9.2 ms\n"
        self.assertEqual(router.parse_traceroute(mac), ["192.168.4.1", "192.168.1.254", None, "12.83.1.1"])
        win = "\nTracing route to 1.1.1.1 over a maximum of 4 hops\n\n  1     2 ms     1 ms     1 ms  192.168.4.1\n  2     *        *        *     Request timed out.\n  3    11 ms    10 ms    12 ms  96.120.4.1\n"
        self.assertEqual(router.parse_traceroute(win), ["192.168.4.1", None, "96.120.4.1"])
        tracepath = " 1?: [LOCALHOST]                      pmtu 1500\n 1:  192.168.1.1                                           0.519ms\n 1:  192.168.1.1                                           0.400ms\n 2:  100.64.0.1                                            8.111ms\n"
        self.assertEqual(router.parse_traceroute(tracepath), ["192.168.1.1", "100.64.0.1"])

    def test_double_nat_from_private_wan_address(self):
        verdict = router.judge_nat("192.168.4.1", ["192.168.4.1", "192.168.1.254", "12.83.1.1"], wan_ip="192.168.1.64")
        self.assertTrue(verdict["double_nat"])

    def test_double_nat_from_second_home_router(self):
        verdict = router.judge_nat("192.168.4.1", ["192.168.4.1", "192.168.0.1"], admin_check=lambda ip: False)
        self.assertTrue(verdict["double_nat"])
        verdict = router.judge_nat("192.168.4.1", ["192.168.4.1", "10.0.0.1"], admin_check=lambda ip: True)
        self.assertTrue(verdict["double_nat"])

    def test_provider_private_hop_is_not_double_nat(self):
        verdict = router.judge_nat("192.168.1.1", ["192.168.1.1", "10.61.0.1", "68.86.1.1"], admin_check=lambda ip: False)
        self.assertIsNone(verdict["double_nat"])
        self.assertTrue(verdict["evidence"])

    def test_single_router(self):
        verdict = router.judge_nat("192.168.1.1", ["192.168.1.1", "96.120.4.1"], wan_ip="73.12.4.9", public_ip="73.12.4.9")
        self.assertEqual((verdict["double_nat"], verdict["cgnat"]), (False, False))

    def test_carrier_nat(self):
        self.assertTrue(router.judge_nat("192.168.12.1", ["192.168.12.1", "100.64.0.1"])["cgnat"])
        self.assertTrue(router.judge_nat("192.168.1.1", [], wan_ip="100.70.1.2")["cgnat"])
        self.assertTrue(router.judge_nat("192.168.1.1", [], wan_ip="73.1.1.1", public_ip="64.2.2.2")["cgnat"])

    def test_brand_hints(self):
        self.assertEqual(router.brand_from_text("<title>NETGEAR Router R7000</title>"), "NETGEAR")
        self.assertEqual(router.brand_from_text("Xfinity xFi Gateway"), "Xfinity gateway")
        self.assertIsNone(router.brand_from_text("Welcome"))


def make(vendor=None, **extra):
    record = {"ip": "192.168.1.50", "mac": "aa:bb:cc:00:00:01", "vendor": vendor, "random_mac": False, "hostname": None,
              "self": False, "gateway": False, "mdns": None, "ssdp": None, "ports": []}
    record.update(extra)
    return record


class DeviceTests(unittest.TestCase):
    def kind(self, **record):
        return devices.classify(make(**record))["kind"]

    def test_gateways(self):
        self.assertEqual(devices.classify(make("eero inc.", gateway=True))["label"], "eero gateway (main router)")
        isp = devices.classify(make("ARRIS Group, Inc.", gateway=True))
        self.assertEqual(isp["kind"], "router")
        self.assertIn("ISP gateway", isp["label"])
        self.assertEqual(devices.classify(make("NETGEAR", gateway=True))["label"], "NETGEAR router")
        self.assertEqual(devices.classify(make(None, gateway=True))["label"], "Main router")

    def test_mesh_and_access_points(self):
        self.assertEqual(self.kind(vendor="eero inc."), "mesh")
        self.assertEqual(self.kind(vendor="Ubiquiti Inc"), "ap")
        self.assertEqual(self.kind(vendor="NETGEAR", hostname="RBS750-satellite"), "mesh")
        self.assertEqual(self.kind(vendor="NETGEAR", hostname="EX6120-extender"), "extender")
        self.assertEqual(self.kind(vendor="TP-LINK TECHNOLOGIES CO.,LTD.", ports=[53, 80]), "ap")
        self.assertEqual(self.kind(vendor="TP-LINK TECHNOLOGIES CO.,LTD.", ports=[9999]), "iot")
        ssdp = {"types": ["urn:schemas-upnp-org:device:InternetGatewayDevice:1"], "description": {"manufacturer": "Linksys"}}
        second = devices.classify(make("Belkin International Inc.", ssdp=ssdp))
        self.assertEqual(second["kind"], "router")
        self.assertIn("second router", second["label"])

    def test_bonjour_devices(self):
        apple_tv = {"services": ["_airplay._tcp"], "txt": {"model": "AppleTV11,1"}, "names": ["Living Room"]}
        self.assertEqual(self.kind(vendor="Apple, Inc.", mdns=apple_tv), "streamer")
        homepod = {"services": ["_airplay._tcp", "_raop._tcp"], "txt": {"model": "AudioAccessory6,1"}, "names": []}
        self.assertEqual(self.kind(vendor="Apple, Inc.", mdns=homepod), "speaker")
        printer = {"services": ["_ipp._tcp"], "txt": {"ty": "Brother HL-L2350DW"}, "names": []}
        self.assertEqual(devices.classify(make("Brother Industries, LTD.", mdns=printer))["label"], "Brother HL-L2350DW")
        cast = {"services": ["_googlecast._tcp"], "txt": {"md": "Nest Mini", "fn": "Kitchen speaker"}, "names": []}
        self.assertEqual(self.kind(vendor="Google, Inc.", mdns=cast), "assistant")
        camera = {"services": ["_hap._tcp"], "txt": {"ci": "17"}, "names": []}
        self.assertEqual(self.kind(mdns=camera), "camera")

    def test_names_ports_and_vendors(self):
        self.assertEqual(self.kind(hostname="Johns-iPhone"), "phone")
        self.assertEqual(self.kind(vendor="Espressif Inc."), "iot")
        self.assertEqual(self.kind(vendor="Sonos, Inc."), "speaker")
        self.assertEqual(self.kind(vendor="Nintendo Co.,Ltd"), "console")
        self.assertEqual(self.kind(ports=[62078]), "phone")
        self.assertEqual(self.kind(ports=[9100]), "printer")
        self.assertEqual(self.kind(vendor="ARRIS Group, Inc."), "modem")
        self.assertEqual(self.kind(random_mac=True), "phone")
        self.assertEqual(self.kind(), "unknown")
        self.assertEqual(devices.classify(make(self=True))["label"], "This computer")

    def test_display_name(self):
        self.assertEqual(devices.display_name(make(hostname="Roku-Bedroom.lan")), "Roku-Bedroom")
        named = make(mdns={"names": ["AABBCCDDEEFF@Den"], "txt": {"fn": "Den TV"}, "services": []})
        self.assertEqual(devices.display_name(named), "Den TV")
        self.assertIsNone(devices.display_name(make(hostname="192.168.1.50")))


class OuiTests(unittest.TestCase):
    def test_lookup(self):
        self.assertGreater(oui.size(), 30000)
        self.assertEqual(oui.lookup("08:f0:1e:11:22:33"), "eero inc.")
        self.assertEqual(oui.lookup("b8:27:eb:00:00:01"), "Raspberry Pi Foundation")

    def test_private_addresses_have_no_vendor(self):
        self.assertTrue(oui.is_random("f2:11:22:33:44:55"))
        self.assertFalse(oui.is_random("a4:83:e7:11:22:33"))
        self.assertIsNone(oui.lookup("f2:11:22:33:44:55"))
        self.assertIsNone(oui.lookup(None))


class ScanMergeTests(unittest.TestCase):
    def test_merge_and_finalize(self):
        import ipaddress
        net = {"local_ip": "192.168.4.23", "gateway": "192.168.4.1", "mac": "a4:83:e7:11:22:33", "hostname": "laptop",
               "network": "192.168.4.0/22"}
        arp = [("192.168.4.1", "08:f0:1e:10:20:30", "en0"), ("192.168.4.20", "08:f0:1e:10:21:40", "en0"),
               ("192.168.4.40", "f2:11:9a:33:44:55", "en0"), ("10.9.9.9", "aa:bb:cc:dd:ee:ff", "utun3"),
               ("192.168.7.255", "ff:ff:ff:ff:ff:ff", "en0")]
        mdns = {"192.168.4.30": {"names": ["Living Room"], "services": ["_airplay._tcp"], "txt": {"model": "AppleTV11,1"}, "hostname": None}}
        records = scan.merge(net, arp, mdns, {}, ipaddress.ip_network("192.168.4.0/22"))
        self.assertEqual(sorted(records), ["192.168.4.1", "192.168.4.20", "192.168.4.23", "192.168.4.30", "192.168.4.40"])
        devices_found = scan.finalize(records)
        by_ip = {d["ip"]: d for d in devices_found}
        self.assertEqual(by_ip["192.168.4.1"]["label"], "eero gateway (main router)")
        self.assertEqual(by_ip["192.168.4.20"]["kind"], "mesh")
        self.assertEqual(by_ip["192.168.4.23"]["label"], "This computer")
        self.assertEqual(by_ip["192.168.4.30"]["name"], "Living Room")
        self.assertTrue(by_ip["192.168.4.40"]["random_mac"])
        summary = scan.summarize(devices_found)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(len(summary["infrastructure"]), 2)

    def test_scan_range_limits_big_networks(self):
        self.assertEqual(str(scan.scan_range({"local_ip": "10.1.2.3", "network": "10.0.0.0/8"})), "10.1.2.0/24")
        self.assertEqual(str(scan.scan_range({"local_ip": "192.168.4.23", "network": "192.168.4.0/22"})), "192.168.4.0/22")
        self.assertIsNone(scan.scan_range({"local_ip": None}))

    def test_parallel_abandons_slow_work(self):
        import time
        results = scan.parallel(lambda n: time.sleep(n) or n, [0.01, 5], workers=2, timeout=0.5)
        self.assertEqual(results, {0.01: 0.01})


SB6141 = """<html><head><title>Signal</title></head><body>
<table><tbody>
<tr><th colspan=2>Downstream</th><th colspan=4>Bonding Channel Value</th></tr>
<tr><td>Channel ID</td><td>1&nbsp;</td><td>2&nbsp;</td><td>3&nbsp;</td><td>4&nbsp;</td></tr>
<tr><td>Frequency</td><td>555000000 Hz&nbsp;</td><td>561000000 Hz&nbsp;</td><td>567000000 Hz&nbsp;</td><td>573000000 Hz&nbsp;</td></tr>
<tr><td>Signal to Noise Ratio</td><td>38 dB&nbsp;</td><td>38 dB&nbsp;</td><td>37 dB&nbsp;</td><td>29 dB&nbsp;</td></tr>
<tr><td>Downstream Modulation</td><td>QAM256&nbsp;</td><td>QAM256&nbsp;</td><td>QAM256&nbsp;</td><td>QAM256&nbsp;</td></tr>
<tr><td>Power Level<table><tr><td>The Downstream Power Level reading is a snapshot</td></tr></table></td><td>2 dBmV</td><td>1 dBmV</td><td>-1 dBmV</td><td>-12 dBmV</td></tr>
</tbody></table>
<table><tbody>
<tr><th>Upstream</th><th colspan=2>Bonding Channel Value</th></tr>
<tr><td>Channel ID</td><td>5&nbsp;</td><td>6&nbsp;</td></tr>
<tr><td>Frequency</td><td>35600000 Hz&nbsp;</td><td>29200000 Hz&nbsp;</td></tr>
<tr><td>Symbol Rate</td><td>5.120 Msym/sec&nbsp;</td><td>5.120 Msym/sec&nbsp;</td></tr>
<tr><td>Power Level</td><td>52 dBmV&nbsp;</td><td>51 dBmV&nbsp;</td></tr>
<tr><td>Upstream Modulation</td><td>[3] QPSK<br>[3] 64QAM</td><td>[3] QPSK</td></tr>
</tbody></table></body></html>"""

NETGEAR = """<html><head><title>NETGEAR Gateway CM1000</title><script>
function InitDsTableTagValue()
{
    /* var tagValueList = '1|1|Locked|QAM256|1|100000000 Hz|0.0|0.0|0|0|'; */
    var tagValueList = '2|1|Locked|QAM256|13|483000000 Hz|6.4|40.9|120|3|2|Locked|QAM256|14|489000000 Hz|-11.5|31.2|99|4000|';
    return tagValueList.split("|");
}
function InitUsTableTagValue()
{
    var tagValueList = '2|1|Locked|ATDMA|1|5120 Ksym/sec|35600000 Hz|44.5 dBmV|2|Not Locked|Unknown|0|0 Ksym/sec|0 Hz|0.0 dBmV|';
    return tagValueList.split("|");
}
</script></head><body></body></html>"""


class ModemTests(unittest.TestCase):
    def test_transposed_tables(self):
        page = modem.parse_page(SB6141)
        self.assertEqual(page["status"], "ok")
        self.assertEqual(len(page["downstream"]), 4)
        self.assertEqual(len(page["upstream"]), 2)
        summary = page["summary"]
        self.assertEqual((summary["down_power_min"], summary["down_power_max"], summary["snr_min"]), (-12.0, 2.0, 29.0))
        self.assertEqual(summary["up_power_max"], 52.0)
        self.assertEqual(page["downstream"][0]["frequency"], 555.0)

    def test_script_embedded_tables(self):
        page = modem.parse_page(NETGEAR)
        self.assertEqual(page["status"], "ok")
        self.assertEqual(len(page["downstream"]), 2)
        self.assertEqual(len(page["upstream"]), 1)   # the unlocked upstream channel is dropped
        self.assertEqual(page["summary"]["uncorrected"], 4003)
        self.assertEqual(page["upstream"][0]["power"], 44.5)

    def test_json_channel_lists(self):
        down = '[{"portId":"1","frequency":"495000000","modulation":"2","signalStrength":"3.400","snr":"40.366","channelId":"13","correcteds":"0","uncorrect":"7"}]'
        page = modem.parse_page(down)
        self.assertEqual((page["status"], page["downstream"][0]["power"], page["summary"]["uncorrected"]), ("ok", 3.4, 7))

    def test_cellular_gateway(self):
        text = '{"device":{"manufacturer":"Arcadyan","model":"KVD21"},"signal":{"4g":{"bands":["b66"],"bars":3.0,"rsrp":-104,"rsrq":-12,"sinr":4},"5g":{"bands":["n41"],"bars":4.0,"rsrp":-93,"rsrq":-11,"sinr":17}}}'
        page = modem.parse_page(text)
        self.assertEqual((page["status"], page["kind"], page["model"]), ("ok", "cellular", "Arcadyan KVD21"))
        self.assertEqual(page["cellular"]["5g"]["rsrp"], -93)

    def test_login_page_and_empty(self):
        self.assertEqual(modem.parse_page('<html><title>Login</title><form><input type="password"></form></html>')["status"], "login_required")
        self.assertEqual(modem.parse_page("")["status"], "no_data")
        self.assertEqual(modem.parse_page("<html><body>hello</body></html>")["status"], "no_data")

    def test_frequency_units(self):
        self.assertEqual(modem._frequency_mhz("507000000 Hz"), 507.0)
        self.assertEqual(modem._frequency_mhz("507.0 MHz"), 507.0)
        self.assertEqual(modem._frequency_mhz("5120 kHz"), 5.12)


if __name__ == "__main__":
    unittest.main()
