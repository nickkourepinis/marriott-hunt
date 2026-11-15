"""Whole-computer simulations: the collectors run against recorded command output for
a Mac, a Windows PC and a Linux laptop, to exercise the platform-specific branches."""
import contextlib
import re
import unittest
from unittest import mock

from wifiscout import channels, cli, netinfo, osutil, router, speed, wifi
from tests import test_netinfo as N
from tests import test_wifi as W


class FakeSystem(object):
    """Stands in for the operating system's command-line tools."""

    def __init__(self, platform, commands, files=None, folders=()):
        self.platform = platform
        self.commands = commands      # list of (prefix, text or function(cmd) -> text)
        self.files = files or {}
        self.folders = set(folders)
        self.calls = []

    def run(self, cmd, timeout=10.0):
        line = " ".join(str(part) for part in cmd)
        self.calls.append(line)
        for prefix, output in self.commands:
            if line.startswith(prefix):
                return 0, output(cmd) if callable(output) else output
        return None, ""

    def which(self, name):
        return name if any(prefix.split()[0] == name for prefix, _ in self.commands) else None

    def read_file(self, path, limit=1 << 20):
        return self.files.get(path, "")

    @contextlib.contextmanager
    def active(self):
        real_isdir = netinfo.os.path.isdir
        with mock.patch.object(osutil, "PLATFORM", self.platform), \
                mock.patch.object(osutil, "run", self.run), \
                mock.patch.object(osutil, "which", self.which), \
                mock.patch.object(osutil, "read_file", self.read_file), \
                mock.patch.object(netinfo.os.path, "isdir", lambda p: p in self.folders or real_isdir(p)):
            yield self


def unix_ping(loss_hosts=(), base_ms=3.0):
    def output(cmd):
        host = cmd[-1]
        count = int(cmd[cmd.index("-c") + 1])
        if host in loss_hosts:
            return "PING %s: 56 data bytes\n\n--- %s ping statistics ---\n%d packets transmitted, 0 packets received, 100.0%% packet loss\n" % (host, host, count)
        lines = ["64 bytes from %s: icmp_seq=%d ttl=64 time=%.3f ms" % (host, i, base_ms + (i % 3) * 0.4) for i in range(count)]
        return "PING %s (%s): 56 data bytes\n%s\n\n--- %s ping statistics ---\n%d packets transmitted, %d packets received, 0.0%% packet loss\n" % (
            host, host, "\n".join(lines), host, count, count)
    return output


def windows_ping(cmd):
    host = cmd[-1]
    count = int(cmd[cmd.index("-n") + 1])
    lines = ["Reply from %s: bytes=32 time=%dms TTL=64" % (host, 4 + i % 2) for i in range(count)]
    return "\nPinging %s with 32 bytes of data:\n%s\n\nPing statistics for %s:\n    Packets: Sent = %d, Received = %d, Lost = 0 (0%% loss),\n" % (
        host, "\n".join(lines), host, count, count)


MAC = FakeSystem("mac", [
    ("route -n get default", N.MAC_ROUTE),
    ("ifconfig en0", N.MAC_IFCONFIG),
    ("networksetup -listallhardwareports", N.HARDWARE_PORTS),
    ("scutil --dns", "resolver #1\n  nameserver[0] : 192.168.4.1\n"),
    ("scutil --get LocalHostName", "Study-MacBook\n"),
    ("arp -an", N.MAC_ARP),
    ("ping -n -c", unix_ping(loss_hosts=("9.9.9.9",))),
    ("system_profiler -json SPAirPortDataType", W.SYSTEM_PROFILER),
    ("traceroute -n", "traceroute to 1.1.1.1 (1.1.1.1), 4 hops max, 52 byte packets\n 1  192.168.4.1  2.3 ms\n 2  192.168.1.254  3.9 ms\n 3  12.83.1.1  9 ms\n"),
    ("networkQuality -c", '{"base_rtt": 21.0, "dl_throughput": 318400000, "ul_throughput": 21700000, "responsiveness": 365}'),
])

WINDOWS = FakeSystem("win", [
    ("route print -4", "Active Routes:\nNetwork Destination        Netmask          Gateway       Interface  Metric\n          0.0.0.0          0.0.0.0      192.168.4.1     192.168.4.23     35\n"),
    ("ipconfig /all", N.WIN_IPCONFIG),
    ("arp -a", "\nInterface: 192.168.4.23 --- 0xc\n  Internet Address      Physical Address      Type\n  192.168.4.1           08-f0-1e-10-20-30     dynamic\n  192.168.4.20          08-f0-1e-10-21-40     dynamic\n  192.168.7.255         ff-ff-ff-ff-ff-ff     static\n"),
    ("ping -n", windows_ping),
    ("netsh wlan show interfaces", W.NETSH_INTERFACES),
    ("netsh wlan show networks mode=bssid", W.NETSH_NETWORKS),
    ("tracert -d", "\nTracing route to 1.1.1.1 over a maximum of 4 hops\n\n  1     2 ms     1 ms     1 ms  192.168.4.1\n  2     9 ms     8 ms     9 ms  96.120.4.1\n\nTrace complete.\n"),
])

LINUX = FakeSystem("linux", [
    ("ip route show default", "default via 192.168.1.1 dev wlp3s0 proto dhcp src 192.168.1.23 metric 600\n"),
    ("ip -o -4 addr show", "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n3: wlp3s0    inet 192.168.1.23/24 brd 192.168.1.255 scope global dynamic wlp3s0\\       valid_lft 86000sec\n"),
    ("ip neigh show", N.LINUX_NEIGH),
    ("ping -n -c", unix_ping()),
    ("nmcli -t -f", W.NMCLI),
    ("iw dev wlp3s0 link", W.IW_LINK),
    ("iw dev", "phy#0\n\tInterface wlp3s0\n\t\tifindex 3\n\t\ttype managed\n"),
    ("resolvectl status", "Link 3 (wlp3s0)\n    Current DNS Server: 192.168.1.1\n"),
    ("tracepath -n", " 1?: [LOCALHOST]                      pmtu 1500\n 1:  192.168.1.1                                           0.5ms\n 2:  100.64.0.1                                            8.1ms\n"),
], files={"/sys/class/net/wlp3s0/address": "3c:2a:f4:00:11:22\n", "/proc/net/wireless": W.PROC_WIRELESS,
          "/etc/resolv.conf": "nameserver 127.0.0.53\n"},
    folders=("/sys/class/net/wlp3s0/wireless", "/sys/class/net/wlp3s0"))


class MacSimulation(unittest.TestCase):
    def test_network(self):
        with MAC.active():
            net = netinfo.get_network()
        self.assertEqual((net["local_ip"], net["gateway"], net["iface"], net["prefix"]), ("192.168.4.23", "192.168.4.1", "en0", 22))
        self.assertEqual((net["network"], net["via"], net["mac"], net["dns"]), ("192.168.4.0/22", "wifi", "a4:83:e7:11:22:33", ["192.168.4.1"]))

    def test_arp_and_ping(self):
        with MAC.active():
            entries = netinfo.arp_table()
            good = netinfo.ping("192.168.4.1", count=6)
            dead = netinfo.ping("9.9.9.9", count=4)
        self.assertEqual(len(entries), 4)
        self.assertEqual((good["method"], good["sent"], good["loss_pct"]), ("icmp", 6, 0.0))
        self.assertAlmostEqual(good["median"], 3.4, places=1)
        self.assertEqual((dead["method"], dead["loss_pct"], dead["median"]), ("icmp", 100.0, None))
        ping_call = [c for c in MAC.calls if c.startswith("ping") and c.endswith("192.168.4.1")][-1]
        self.assertIn("-W 1000", ping_call)   # macOS takes the reply timeout in milliseconds

    def test_wifi(self):
        with MAC.active():
            section = wifi.collect("en0")
        link = section["link"]
        self.assertEqual((link["ssid"], link["channel"], link["rssi"], link["snr"]), ("Maple House", 149, -53, 42))
        mine = [n for n in section["neighbors"] if n["mine"]]
        self.assertEqual(len(mine), 1)
        self.assertEqual(channels.analyze(link, section["neighbors"])["density"]["visible"], 2)

    def test_latency_speed_and_route(self):
        with MAC.active():
            profile = speed.latency_profile("192.168.4.1", count=5)
            with mock.patch.object(speed, "fetch_meta", lambda endpoint=None: speed.parse_meta("ip=203.0.113.7\ncolo=EWR\n")):
                result = speed.run_speedtest()
            hops = router.trace_first_hops()
            name = cli.bonjour_name()
        self.assertTrue(profile["online"])
        self.assertEqual(profile["internet_target"], "1.1.1.1")
        self.assertNotIn("9.9.9.9", [a for a, s in profile["anchors"].items() if s.get("median") is not None])
        self.assertEqual((result["method"], result["down_mbps"], result["up_mbps"]), ("networkQuality", 318.4, 21.7))
        self.assertEqual((result["bloat_grade"], result["public_ip_masked"]), ("C", "203.x.x.7"))
        self.assertEqual(hops, ["192.168.4.1", "192.168.1.254", "12.83.1.1"])
        self.assertTrue(router.judge_nat("192.168.4.1", hops)["double_nat"])
        self.assertEqual(name, "Study-MacBook.local")


class FullAuditSimulation(unittest.TestCase):
    """The whole `wifiscout run` pipeline on the simulated Mac, with the network calls canned."""

    def test_audit_to_report(self):
        import shutil
        import tempfile

        from wifiscout import audit, discovery, modem, report, store

        mdns = {"192.168.4.20": {"names": [], "services": [], "txt": {}, "hostname": "eero-hallway.local"}}
        patches = [
            mock.patch.object(netinfo, "sweep", lambda network, skip=(): 0),
            mock.patch.object(discovery, "mdns_scan", lambda local_ip=None, duration=4.0: mdns),
            mock.patch.object(discovery, "ssdp_scan", lambda local_ip=None, duration=3.0, fetch=True: {}),
            mock.patch.object(speed, "dns_query_ms", lambda server, name, timeout=2.0: 90.0 if server.startswith("192.168.") else 20.0),
            mock.patch.object(router, "fetch_text", lambda *args, **kwargs: ""),
            mock.patch.object(modem, "_reachable", lambda address, ports=(80, 443), timeout=0.8: None),
            mock.patch.object(modem, "_get", lambda url, timeout=4.0: ""),
            mock.patch("socket.gethostbyaddr", side_effect=OSError("no name")),
        ]
        folder = tempfile.mkdtemp()
        try:
            with MAC.active():
                for patch in patches:
                    patch.start()
                try:
                    progress = []
                    result = audit.run_audit(say=progress.append, quick=True)
                finally:
                    for patch in patches:
                        patch.stop()
                data = store.DataDir(folder)
                data.save_json("audit.json", result)
                store.Survey(data).set_profile({"home": "house", "plan_down": 500})
                path, bundle = report.write(data)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["network"]["via"], "wifi")
        by_ip = {d["ip"]: d for d in result["scan"]["devices"]}
        self.assertEqual(sorted(by_ip), ["192.168.4.1", "192.168.4.20", "192.168.4.23"])
        self.assertEqual(by_ip["192.168.4.1"]["label"], "eero gateway (main router)")
        self.assertEqual((by_ip["192.168.4.20"]["kind"], by_ip["192.168.4.20"]["name"]), ("mesh", "eero-hallway"))
        self.assertEqual(result["wifi"]["link"]["channel"], 149)
        self.assertTrue(result["latency"]["online"])
        self.assertEqual(result["dns"]["system_avg_ms"], 90.0)
        self.assertIsNone(result["speed"])                      # quick mode skips the speed test
        self.assertEqual(result["router"]["brand"], "eero")
        self.assertEqual(result["modem"]["status"], "unreachable")
        analysis = bundle["analysis"]
        found = [f["id"] for f in analysis["findings"]]
        self.assertIn("dns_slow", found)
        self.assertIn("no_survey", found)
        self.assertTrue(analysis["context"]["is_eero"] and analysis["context"]["is_mesh"])
        self.assertEqual(bundle["meta"]["command"], "python3 -m wifiscout")
        self.assertTrue(any("Scanning the network" in line for line in progress))


class WindowsSimulation(unittest.TestCase):
    def test_network(self):
        with WINDOWS.active():
            net = netinfo.get_network()
        self.assertEqual((net["local_ip"], net["gateway"], net["prefix"], net["via"]), ("192.168.4.23", "192.168.4.1", 22, "wifi"))
        self.assertEqual((net["iface"], net["mac"], net["dns"]), ("Wireless LAN adapter Wi-Fi", "12:34:56:78:9a:bc", ["192.168.4.1", "1.1.1.1"]))

    def test_arp_ping_wifi_route(self):
        with WINDOWS.active():
            entries = netinfo.usable_arp(netinfo.arp_table())
            stats = netinfo.ping("192.168.4.1", count=20)
            section = wifi.collect()
            hops = router.trace_first_hops()
            command = osutil.command_name()
        self.assertEqual([e[0] for e in entries], ["192.168.4.1", "192.168.4.20"])
        self.assertEqual((stats["method"], stats["sent"], stats["loss_pct"]), ("icmp", 10, 0.0))   # capped: Windows pings once a second
        self.assertEqual(stats["median"], 4.5)
        self.assertEqual((section["link"]["ssid"], section["link"]["rssi"], section["link"]["band"]), ("Maple House", -57, "5"))
        self.assertEqual(len(section["neighbors"]), 4)
        self.assertTrue(any("percentage" in note for note in section["notes"]))
        self.assertEqual(hops, ["192.168.4.1", "96.120.4.1"])
        self.assertFalse(router.judge_nat("192.168.4.1", hops)["double_nat"])
        self.assertTrue(command.startswith("py "))


class LinuxSimulation(unittest.TestCase):
    def test_network(self):
        with LINUX.active():
            net = netinfo.get_network()
        self.assertEqual((net["local_ip"], net["gateway"], net["iface"], net["prefix"], net["via"]), ("192.168.1.23", "192.168.1.1", "wlp3s0", 24, "wifi"))
        self.assertEqual((net["mac"], net["dns"]), ("3c:2a:f4:00:11:22", ["192.168.1.1"]))   # looks past the local stub resolver

    def test_wifi_prefers_exact_signal_from_iw(self):
        with LINUX.active():
            section = wifi.collect("wlp3s0")
        link = section["link"]
        self.assertEqual((link["ssid"], link["bssid"], link["channel"], link["source"]), ("Maple House", "08:f0:1e:10:21:42", 44, "iw"))
        self.assertEqual((link["rssi"], link["width"], link["generation"]), (-57, 80, "Wi-Fi 5"))
        self.assertEqual(len(section["neighbors"]), 3)
        self.assertTrue(section["neighbors"][0]["mine"])

    def test_route_shows_carrier_nat(self):
        with LINUX.active():
            entries = netinfo.arp_table()
            hops = router.trace_first_hops()
        self.assertEqual(len(entries), 2)
        self.assertEqual(hops, ["192.168.1.1", "100.64.0.1"])
        self.assertTrue(router.judge_nat("192.168.1.1", hops)["cgnat"])


class PingFallbackTests(unittest.TestCase):
    def test_retries_without_fast_interval_when_refused(self):
        calls = []

        def run(cmd, timeout=10.0):
            calls.append(list(cmd))
            if "-i" in cmd:
                return 2, "ping: -i interval too short: Operation not permitted\n"
            count = int(cmd[cmd.index("-c") + 1])
            return 0, "\n".join("64 bytes from 10.0.0.1: icmp_seq=%d ttl=64 time=2.0 ms" % i for i in range(count)) + \
                "\n%d packets transmitted, %d packets received, 0.0%% packet loss\n" % (count, count)

        with mock.patch.object(osutil, "PLATFORM", "linux"), mock.patch.object(osutil, "run", run), \
                mock.patch.object(osutil, "which", lambda name: name):
            stats = netinfo.ping("10.0.0.1", count=10)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("-i", calls[1])
        self.assertEqual((stats["method"], stats["sent"], stats["median"]), ("icmp", 5, 2.0))

    def test_command_name_for_single_file(self):
        with mock.patch.object(osutil.sys, "argv", ["/home/me/wifiscout.pyz", "run"]), mock.patch.object(osutil, "PLATFORM", "mac"):
            self.assertEqual(osutil.command_name(), "python3 wifiscout.pyz")
        self.assertTrue(re.fullmatch(r"(py|python3) (-m wifiscout|\S+\.pyz)", osutil.command_name()))


if __name__ == "__main__":
    unittest.main()
