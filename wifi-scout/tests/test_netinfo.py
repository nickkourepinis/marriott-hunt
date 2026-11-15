import ipaddress
import unittest

from wifiscout import netinfo

MAC_ARP = """? (192.168.4.1) at 8:f0:1e:10:20:30 on en0 ifscope [ethernet]
? (192.168.4.20) at 8:f0:1e:10:21:40 on en0 ifscope [ethernet]
? (192.168.4.33) at (incomplete) on en0 ifscope [ethernet]
? (192.168.7.255) at ff:ff:ff:ff:ff:ff on en0 ifscope [ethernet]
? (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]
"""

LINUX_NEIGH = """192.168.1.1 dev wlp3s0 lladdr a4:11:62:aa:bb:cc REACHABLE
192.168.1.50 dev wlp3s0 lladdr 3c:2a:f4:77:88:99 STALE
192.168.1.77 dev wlp3s0  FAILED
fe80::1 dev wlp3s0 lladdr a4:11:62:aa:bb:cc router REACHABLE
"""

WIN_ARP = """
Interface: 192.168.1.23 --- 0xc
  Internet Address      Physical Address      Type
  192.168.1.1           a4-11-62-aa-bb-cc     dynamic
  192.168.1.50          3c-2a-f4-77-88-99     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
"""

PROC_ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.0.2.1        0x1         0x2         02:fc:00:00:00:05     *        eth0
192.0.2.9        0x1         0x0         00:00:00:00:00:00     *        eth0
"""

MAC_ROUTE = """   route to: default
destination: default
       mask: default
    gateway: 192.168.4.1
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
 recvpipe  sendpipe  ssthresh  rtt,msec    rttvar  hopcount      mtu     expire
       0         0         0         0         0         0      1500         0
"""

LINUX_ROUTE = """default via 192.168.1.1 dev wlp3s0 proto dhcp src 192.168.1.23 metric 600
default via 192.168.1.1 dev enp4s0 proto dhcp src 192.168.1.9 metric 100
"""

PROC_ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t010200C0\t0003\t0\t0\t0\t00000000\t0\t0\t0
eth0\t000200C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0
"""

WIN_ROUTE = """IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.1.1     192.168.1.23     35
          0.0.0.0          0.0.0.0      192.168.1.1      192.168.1.9     25
        127.0.0.0        255.0.0.0         On-link         127.0.0.1    331
"""

MAC_IFCONFIG = """en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\toptions=6460<TSO4,TSO6,CHANNEL_IO,PARTIAL_CSUM,ZEROINVERT_CSUM>
\tether a4:83:e7:11:22:33
\tinet6 fe80::1c2b:3d4e:5f60:7182%en0 prefixlen 64 secured scopeid 0xb
\tinet 192.168.4.23 netmask 0xfffffc00 broadcast 192.168.7.255
\tnd6 options=201<PERFORMNUD,DAD>
\tmedia: autoselect
\tstatus: active
"""

WIN_IPCONFIG = """
Windows IP Configuration

   Host Name . . . . . . . . . . . . : DESKTOP-ABC
   Primary Dns Suffix  . . . . . . . :
   Node Type . . . . . . . . . . . . : Hybrid

Ethernet adapter Ethernet:

   Media State . . . . . . . . . . . : Media disconnected
   Connection-specific DNS Suffix  . :
   Physical Address. . . . . . . . . : AA-BB-CC-00-11-22

Wireless LAN adapter Wi-Fi:

   Connection-specific DNS Suffix  . : lan
   Description . . . . . . . . . . . : Intel(R) Wi-Fi 6 AX201 160MHz
   Physical Address. . . . . . . . . : 12-34-56-78-9A-BC
   DHCP Enabled. . . . . . . . . . . : Yes
   Link-local IPv6 Address . . . . . : fe80::1234:5678:9abc:def0%12(Preferred)
   IPv4 Address. . . . . . . . . . . : 192.168.4.23(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.252.0
   Lease Obtained. . . . . . . . . . : Saturday, November 14, 2026 10:23:45 AM
   Default Gateway . . . . . . . . . : fe80::1%12
                                       192.168.4.1
   DHCP Server . . . . . . . . . . . : 192.168.4.1
   DHCPv6 Client DUID. . . . . . . . : 00-01-00-01-2A-BC-DE-F0-12-34-56-78-9A-BC
   DNS Servers . . . . . . . . . . . : 192.168.4.1
                                       1.1.1.1
   NetBIOS over Tcpip. . . . . . . . : Enabled
"""

GERMAN_IPCONFIG = """
Drahtlos-LAN-Adapter WLAN:

   Verbindungsspezifisches DNS-Suffix: fritz.box
   Physische Adresse . . . . . . . . : 12-34-56-78-9A-BC
   IPv4-Adresse  . . . . . . . . . . : 192.168.178.40(Bevorzugt)
   Subnetzmaske  . . . . . . . . . . : 255.255.255.0
   Standardgateway . . . . . . . . . : 192.168.178.1
   DNS-Server  . . . . . . . . . . . : 192.168.178.1
"""

MAC_PING = """PING 192.168.4.1 (192.168.4.1): 56 data bytes
64 bytes from 192.168.4.1: icmp_seq=0 ttl=64 time=2.345 ms
64 bytes from 192.168.4.1: icmp_seq=1 ttl=64 time=3.012 ms
Request timeout for icmp_seq 2
64 bytes from 192.168.4.1: icmp_seq=3 ttl=64 time=2.801 ms

--- 192.168.4.1 ping statistics ---
4 packets transmitted, 3 packets received, 25.0% packet loss
round-trip min/avg/max/stddev = 2.345/2.719/3.012/0.279 ms
"""

LINUX_PING = """PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=11.6 ms
64 bytes from 1.1.1.1: icmp_seq=2 ttl=57 time=12.1 ms

--- 1.1.1.1 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
rtt min/avg/max/mdev = 11.600/11.850/12.100/0.250 ms
"""

WIN_PING = """
Pinging 192.168.1.1 with 32 bytes of data:
Reply from 192.168.1.1: bytes=32 time=2ms TTL=64
Reply from 192.168.1.1: bytes=32 time<1ms TTL=64
Request timed out.
Reply from 192.168.1.23: Destination host unreachable.

Ping statistics for 192.168.1.1:
    Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),
Approximate round trip times in milli-seconds:
    Minimum = 0ms, Maximum = 2ms, Average = 1ms
"""

GERMAN_PING = """
Ping wird ausgeführt für 192.168.1.1 mit 32 Bytes Daten:
Antwort von 192.168.1.1: Bytes=32 Zeit=3ms TTL=64
Antwort von 192.168.1.1: Bytes=32 Zeit<1ms TTL=64

Ping-Statistik für 192.168.1.1:
    Pakete: Gesendet = 2, Empfangen = 2, Verloren = 0
"""

HARDWARE_PORTS = """
Hardware Port: Ethernet Adapter (en4)
Device: en4
Ethernet Address: 1e:00:8a:11:22:33

Hardware Port: Wi-Fi
Device: en0
Ethernet Address: a4:83:e7:11:22:33

Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: 36:8a:c9:00:11:22
"""


class MacAddressTests(unittest.TestCase):
    def test_normalize_pads_short_octets(self):
        self.assertEqual(netinfo.normalize_mac("8:f0:1e:10:20:3"), "08:f0:1e:10:20:03")
        self.assertEqual(netinfo.normalize_mac("A4-11-62-AA-BB-CC"), "a4:11:62:aa:bb:cc")

    def test_normalize_rejects_garbage(self):
        self.assertIsNone(netinfo.normalize_mac("(incomplete)"))
        self.assertIsNone(netinfo.normalize_mac("a4:11:62"))
        self.assertIsNone(netinfo.normalize_mac(None))

    def test_address_classes(self):
        self.assertTrue(netinfo.is_private_ip("192.168.4.1"))
        self.assertTrue(netinfo.is_private_ip("10.0.0.1"))
        self.assertFalse(netinfo.is_private_ip("8.8.8.8"))
        self.assertTrue(netinfo.is_cgnat_ip("100.72.3.4"))
        self.assertFalse(netinfo.is_cgnat_ip("100.128.0.1"))


class ArpTests(unittest.TestCase):
    def test_macos(self):
        entries = netinfo.parse_arp_bsd(MAC_ARP)
        self.assertIn(("192.168.4.1", "08:f0:1e:10:20:30", "en0"), entries)
        usable = netinfo.usable_arp(entries, ipaddress.ip_network("192.168.4.0/22"))
        self.assertEqual(sorted(ip for ip, _, _ in usable), ["192.168.4.1", "192.168.4.20"])

    def test_linux(self):
        entries = netinfo.parse_ip_neigh(LINUX_NEIGH)
        self.assertEqual([e[0] for e in entries], ["192.168.1.1", "192.168.1.50"])

    def test_proc(self):
        entries = netinfo.parse_proc_arp(PROC_ARP)
        self.assertEqual(entries, [("192.0.2.1", "02:fc:00:00:00:05", "eth0")])

    def test_windows(self):
        entries = netinfo.parse_arp_win(WIN_ARP)
        usable = netinfo.usable_arp(entries, ipaddress.ip_network("192.168.1.0/24"))
        self.assertEqual(sorted(ip for ip, _, _ in usable), ["192.168.1.1", "192.168.1.50"])
        self.assertEqual(entries[0][2], "192.168.1.23")


class RouteTests(unittest.TestCase):
    def test_macos(self):
        self.assertEqual(netinfo.parse_route_get_mac(MAC_ROUTE), ("192.168.4.1", "en0"))

    def test_linux_prefers_lowest_metric(self):
        self.assertEqual(netinfo.parse_ip_route_linux(LINUX_ROUTE), ("192.168.1.1", "enp4s0"))

    def test_proc_route(self):
        self.assertEqual(netinfo.parse_proc_net_route(PROC_ROUTE), ("192.0.2.1", "eth0"))

    def test_windows_prefers_lowest_metric(self):
        self.assertEqual(netinfo.parse_route_print_win(WIN_ROUTE), ("192.168.1.1", "192.168.1.9"))

    def test_empty_inputs(self):
        self.assertEqual(netinfo.parse_route_get_mac(""), (None, None))
        self.assertEqual(netinfo.parse_ip_route_linux(""), (None, None))
        self.assertEqual(netinfo.parse_route_print_win(""), (None, None))


class AddressTests(unittest.TestCase):
    def test_ifconfig(self):
        items = netinfo.parse_ifconfig(MAC_IFCONFIG, "en0")
        self.assertEqual(items, [{"iface": "en0", "ip": "192.168.4.23", "prefix": 22, "mac": "a4:83:e7:11:22:33"}])

    def test_ip_addr(self):
        text = "2: wlp3s0    inet 192.168.1.23/24 brd 192.168.1.255 scope global dynamic noprefixroute wlp3s0\\       valid_lft 86000sec\n"
        self.assertEqual(netinfo.parse_ip_addr_linux(text)[0]["prefix"], 24)

    def test_ipconfig_english(self):
        adapters = netinfo.parse_ipconfig_win(WIN_IPCONFIG)
        wifi = [a for a in adapters if a["ip"]][0]
        self.assertTrue(wifi["wifi"])
        self.assertEqual(wifi["ip"], "192.168.4.23")
        self.assertEqual(wifi["mask"], "255.255.252.0")
        self.assertEqual(wifi["gateway"], "192.168.4.1")
        self.assertEqual(wifi["dns"], ["192.168.4.1", "1.1.1.1"])
        self.assertEqual(wifi["mac"], "12:34:56:78:9a:bc")
        ethernet = [a for a in adapters if a["name"].startswith("Ethernet")][0]
        self.assertFalse(ethernet["wifi"])
        self.assertIsNone(ethernet["ip"])

    def test_ipconfig_german(self):
        adapter = [a for a in netinfo.parse_ipconfig_win(GERMAN_IPCONFIG) if a["ip"]][0]
        self.assertTrue(adapter["wifi"])
        self.assertEqual((adapter["ip"], adapter["mask"], adapter["gateway"]), ("192.168.178.40", "255.255.255.0", "192.168.178.1"))
        self.assertEqual(adapter["dns"], ["192.168.178.1"])

    def test_hardware_ports(self):
        self.assertEqual(netinfo.parse_hardware_ports(HARDWARE_PORTS), {"en0"})

    def test_dns_sources(self):
        self.assertEqual(netinfo.parse_resolv_conf("# comment\nnameserver 127.0.0.53\nnameserver 1.1.1.1\n"), ["127.0.0.53", "1.1.1.1"])
        scutil = "resolver #1\n  nameserver[0] : 192.168.4.1\n  nameserver[1] : 2001:db8::1\n  if_index : 11 (en0)\n"
        self.assertEqual(netinfo.parse_scutil_dns(scutil), ["192.168.4.1"])
        resolvectl = "Link 3 (wlp3s0)\n    Current DNS Server: 192.168.1.1\n           DNS Servers: 192.168.1.1 9.9.9.9\n"
        self.assertEqual(netinfo.parse_resolvectl(resolvectl), ["192.168.1.1", "9.9.9.9"])


class PingTests(unittest.TestCase):
    def test_macos_with_loss(self):
        parsed = netinfo.parse_ping(MAC_PING)
        self.assertEqual(parsed["sent"], 4)
        self.assertEqual(parsed["times"], [2.345, 3.012, 2.801])
        stats = netinfo.summarize_times(parsed["times"], sent=parsed["sent"])
        self.assertEqual(stats["loss_pct"], 25.0)
        self.assertAlmostEqual(stats["median"], 2.8, places=1)
        self.assertAlmostEqual(stats["jitter"], 0.44, places=2)

    def test_linux(self):
        parsed = netinfo.parse_ping(LINUX_PING)
        self.assertEqual((parsed["sent"], parsed["times"]), (2, [11.6, 12.1]))

    def test_windows_ignores_unreachable_replies(self):
        parsed = netinfo.parse_ping(WIN_PING)
        self.assertEqual(parsed["sent"], 4)
        self.assertEqual(parsed["times"], [2.0, 0.5])
        self.assertEqual(netinfo.summarize_times(parsed["times"], sent=4)["loss_pct"], 50.0)

    def test_windows_german(self):
        parsed = netinfo.parse_ping(GERMAN_PING)
        self.assertEqual((parsed["sent"], parsed["times"]), (2, [3.0, 0.5]))

    def test_no_replies(self):
        stats = netinfo.summarize_times([], sent=5)
        self.assertEqual(stats["loss_pct"], 100.0)
        self.assertIsNone(stats["median"])

    def test_tcp_ping_against_closed_port_counts_as_reply(self):
        stats = netinfo.tcp_ping("127.0.0.1", count=3, ports=(9,), timeout=0.5, interval=0.01)
        self.assertEqual(stats["method"], "tcp")
        self.assertEqual(stats["received"], 3)


if __name__ == "__main__":
    unittest.main()
