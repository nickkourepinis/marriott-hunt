import json
import unittest

from wifiscout import channels, wifi

SYSTEM_PROFILER = json.dumps({"SPAirPortDataType": [{
    "spairport_airport_interfaces": [{
        "_name": "en0",
        "spairport_airport_other_local_wireless_networks": [
            {"_name": "NETGEAR58", "spairport_network_channel": "6 (2GHz, 20MHz)", "spairport_network_phymode": "802.11b/g/n",
             "spairport_network_type": "spairport_network_type_station",
             "spairport_security_mode": "spairport_security_mode_wpa2_personal", "spairport_signal_noise": "-70 dBm / -95 dBm"},
            {"_name": "Maple House", "spairport_network_channel": "6 (2GHz, 20MHz)", "spairport_network_phymode": "802.11ax",
             "spairport_security_mode": "spairport_security_mode_wpa3_transition", "spairport_signal_noise": "-48 dBm / -93 dBm"},
            {"_name": "CoffeeShop", "spairport_network_channel": "37 (6GHz, 160MHz)", "spairport_network_phymode": "802.11ax",
             "spairport_security_mode": "spairport_security_mode_none", "spairport_signal_noise": "-81 dBm / -96 dBm"},
        ],
        "spairport_current_network_information": {
            "_name": "Maple House", "spairport_network_channel": "149 (5GHz, 80MHz)", "spairport_network_country_code": "US",
            "spairport_network_mcs": 9, "spairport_network_phymode": "802.11ac", "spairport_network_rate": 867,
            "spairport_network_type": "spairport_network_type_station",
            "spairport_security_mode": "spairport_security_mode_wpa2_personal", "spairport_signal_noise": "-53 dBm / -95 dBm"},
        "spairport_status_information": "spairport_status_connected",
        "spairport_supported_phymodes": "802.11 a/b/g/n/ac/ax",
        "spairport_wireless_mac_address": "a4:83:e7:11:22:33",
    }],
    "spairport_software_information": {"spairport_corewlan_version": "16.0 (1657)"},
}]})

REDACTED = json.dumps({"SPAirPortDataType": [{"spairport_airport_interfaces": [{
    "_name": "en0",
    "spairport_airport_other_local_wireless_networks": [
        {"_name": "<redacted>", "spairport_network_channel": "44 (5GHz, 80MHz)", "spairport_signal_noise": "-50 dBm / -92 dBm"},
        {"_name": "<redacted>", "spairport_network_channel": "11 (2GHz, 20MHz)", "spairport_signal_noise": "-77 dBm / -92 dBm"}],
    "spairport_current_network_information": {
        "_name": "<redacted>", "spairport_network_channel": "44 (5GHz, 80MHz)", "spairport_network_phymode": "802.11ax",
        "spairport_network_rate": 1200, "spairport_signal_noise": "-47 dBm / -92 dBm",
        "spairport_security_mode": "spairport_security_mode_wpa3_transition"},
}]}]})

AIRPORT_INFO = """     agrCtlRSSI: -55
     agrExtRSSI: 0
    agrCtlNoise: -92
    agrExtNoise: 0
          state: running
        op mode: station
     lastTxRate: 867
        maxRate: 867
lastAssocStatus: 0
    802.11 auth: open
      link auth: wpa2-psk
          BSSID: a4:11:62:aa:bb:cc
           SSID: Maple House
            MCS: 9
        channel: 149,80
"""

AIRPORT_SCAN = """                            SSID BSSID             RSSI CHANNEL HT CC SECURITY (auth/unicast/group)
                     NETGEAR58 10:da:43:11:22:33 -78  6       Y  US WPA2(PSK/AES/AES)
                   Maple House a4:11:62:aa:bb:cd -41  36,+1   Y  US WPA2(PSK/AES/AES)
                    xfinitywifi 12:da:43:11:22:34 -80  11      Y  US NONE
"""

NETSH_INTERFACES = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 11111111-2222-3333-4444-555555555555
    Physical address       : 12:34:56:78:9a:bc
    Interface type         : Primary
    State                  : connected
    SSID                   : Maple House
    BSSID                  : a4:11:62:aa:bb:cc
    Network type           : Infrastructure
    Radio type             : 802.11ax
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Connection mode        : Auto Connect
    Band                   : 5 GHz
    Channel                : 149
    Receive rate (Mbps)    : 1201
    Transmit rate (Mbps)   : 961
    Signal                 : 86%
    Profile                : Maple House

    Hosted network status  : Not available
"""

NETSH_DISCONNECTED = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    State                  : disconnected
    Radio status           : Hardware On
                             Software On
"""

NETSH_NETWORKS = """
Interface name : Wi-Fi
There are 3 networks currently visible.

SSID 1 : Maple House
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : a4:11:62:aa:bb:cc
         Signal             : 86%
         Radio type         : 802.11ax
         Band               : 5 GHz
         Channel            : 149
         Basic rates (Mbps) : 6 12 24
         Other rates (Mbps) : 9 18 36 48 54
    BSSID 2                 : a4:11:62:aa:bb:cd
         Signal             : 70%
         Radio type         : 802.11n
         Band               : 2.4 GHz
         Channel            : 6
         Channel Utilization:   45 (17 %)

SSID 2 : NETGEAR58
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 10:da:43:11:22:33
         Signal             : 40%
         Radio type         : 802.11n
         Channel            : 6

SSID 3 :
    Network type            : Infrastructure
    Authentication          : Open
    Encryption              : None
    BSSID 1                 : 12:da:43:11:22:34
         Signal             : 22%
         Radio type         : 802.11g
         Channel            : 11
"""

NMCLI = """*:Maple House:08\\:F0\\:1E\\:10\\:21\\:42:44:5220 MHz:540 Mbit/s:82:WPA2 WPA3
 :Maple House:08\\:F0\\:1E\\:10\\:20\\:31:6:2437 MHz:130 Mbit/s:64:WPA2 WPA3
 :NETGEAR58:10\\:DA\\:43\\:00\\:00\\:00:6:2437 MHz:195 Mbit/s:47:WPA2
 ::12\\:DA\\:43\\:00\\:00\\:01:11:2462 MHz:54 Mbit/s:22:
"""

IW_LINK = """Connected to 08:f0:1e:10:21:42 (on wlp3s0)
\tSSID: Maple House
\tfreq: 5220
\tRX: 123456789 bytes (98765 packets)
\tTX: 23456789 bytes (45678 packets)
\tsignal: -57 dBm
\trx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
\ttx bitrate: 650.0 MBit/s VHT-MCS 7 80MHz short GI VHT-NSS 2

\tbss flags:\tshort-slot-time
\tdtim period:\t2
"""

PROC_WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
wlp3s0: 0000   60.  -50.  -256        0      0      0      0     45        0
"""


class HelperTests(unittest.TestCase):
    def test_band_of(self):
        self.assertEqual(wifi.band_of(6), "2.4")
        self.assertEqual(wifi.band_of(149), "5")
        self.assertEqual(wifi.band_of(37, hint="6GHz"), "6")
        self.assertEqual(wifi.band_of(None, freq=5955), "6")
        self.assertEqual(wifi.band_of(None, hint="2.4 GHz"), "2.4")

    def test_channel_from_freq(self):
        self.assertEqual(wifi.channel_from_freq(2437), 6)
        self.assertEqual(wifi.channel_from_freq(5220), 44)
        self.assertEqual(wifi.channel_from_freq(5955), 1)

    def test_generation(self):
        self.assertEqual(wifi.wifi_generation("802.11ax"), "Wi-Fi 6")
        self.assertEqual(wifi.wifi_generation("802.11ax", "6"), "Wi-Fi 6E")
        self.assertEqual(wifi.wifi_generation("802.11be"), "Wi-Fi 7")
        self.assertEqual(wifi.wifi_generation("802.11ac"), "Wi-Fi 5")
        self.assertEqual(wifi.wifi_generation("802.11b/g/n"), "Wi-Fi 4")
        self.assertEqual(wifi.wifi_generation("802.11g"), "Wi-Fi 3 or older")

    def test_guess_band_from_iphone_channel(self):
        self.assertEqual(wifi.guess_band(6), "2.4")
        self.assertEqual(wifi.guess_band(44), "5")
        self.assertEqual(wifi.guess_band(149), "5")
        self.assertEqual(wifi.guess_band(37), "6")
        self.assertEqual(wifi.guess_band(5, tx_rate=1200, standard="802.11ax"), "6")
        self.assertEqual(wifi.guess_band(5, tx_rate=72, standard="802.11n"), "2.4")
        self.assertIsNone(wifi.guess_band(None))

    def test_quality_to_dbm(self):
        self.assertEqual(wifi.quality_to_dbm(100), -50)
        self.assertEqual(wifi.quality_to_dbm(50), -75)
        self.assertEqual(wifi.quality_to_dbm(0), -100)


class MacTests(unittest.TestCase):
    def test_system_profiler(self):
        link, neighbors = wifi.parse_system_profiler(SYSTEM_PROFILER)
        self.assertTrue(link["connected"])
        self.assertEqual((link["ssid"], link["channel"], link["band"], link["width"]), ("Maple House", 149, "5", 80))
        self.assertEqual((link["rssi"], link["noise"], link["snr"]), (-53, -95, 42))
        self.assertEqual((link["tx_rate"], link["generation"], link["security"]), (867, "Wi-Fi 5", "WPA2 Personal"))
        self.assertEqual(len(neighbors), 3)
        self.assertEqual(neighbors[2]["band"], "6")
        self.assertEqual(neighbors[2]["security"], "Open")
        self.assertEqual(neighbors[1]["security"], "WPA2/WPA3 Personal")

    def test_redacted_names(self):
        link, neighbors = wifi.parse_system_profiler(REDACTED)
        self.assertTrue(link["connected"])
        self.assertIsNone(link["ssid"])
        self.assertIsNone(neighbors[0]["ssid"])
        wifi._mark_mine(link, neighbors)
        self.assertTrue(neighbors[0]["mine"])   # strong, same channel: our own mesh node
        self.assertFalse(neighbors[1]["mine"])

    def test_garbage_json(self):
        link, neighbors = wifi.parse_system_profiler("not json")
        self.assertFalse(link["connected"])
        self.assertEqual(neighbors, [])

    def test_airport_info(self):
        link = wifi.parse_airport_info(AIRPORT_INFO)
        self.assertEqual((link["ssid"], link["bssid"], link["rssi"], link["noise"]), ("Maple House", "a4:11:62:aa:bb:cc", -55, -92))
        self.assertEqual((link["channel"], link["width"], link["security"]), (149, 80, "WPA2 Personal"))

    def test_airport_scan(self):
        neighbors = wifi.parse_airport_scan(AIRPORT_SCAN)
        self.assertEqual([n["ssid"] for n in neighbors], ["NETGEAR58", "Maple House", "xfinitywifi"])
        self.assertEqual((neighbors[1]["channel"], neighbors[1]["width"], neighbors[1]["rssi"]), (36, 40, -41))
        self.assertEqual(neighbors[2]["security"], "Open")


class WindowsTests(unittest.TestCase):
    def test_interfaces(self):
        link = wifi.parse_netsh_interfaces(NETSH_INTERFACES)
        self.assertTrue(link["connected"])
        self.assertEqual((link["ssid"], link["bssid"], link["channel"], link["band"]), ("Maple House", "a4:11:62:aa:bb:cc", 149, "5"))
        self.assertEqual((link["rx_rate"], link["tx_rate"], link["rssi"]), (1201, 961, -57))
        self.assertEqual((link["generation"], link["security"]), ("Wi-Fi 6", "WPA2-Personal"))
        self.assertIn("rssi_estimated_from_percent", link["notes"])

    def test_disconnected(self):
        link = wifi.parse_netsh_interfaces(NETSH_DISCONNECTED)
        self.assertFalse(link["connected"])

    def test_location_permission_message(self):
        text = "Network shell commands need location permission to access WLAN information. Turn on Location services."
        link = wifi.parse_netsh_interfaces(text)
        self.assertFalse(link["connected"])
        self.assertIn("windows_location_permission", link["notes"])

    def test_networks(self):
        neighbors = wifi.parse_netsh_networks(NETSH_NETWORKS)
        self.assertEqual(len(neighbors), 4)
        first, second, third, fourth = neighbors
        self.assertEqual((first["ssid"], first["channel"], first["band"], first["rssi"]), ("Maple House", 149, "5", -57))
        self.assertEqual((second["channel"], second["band"], second["utilization"]), (6, "2.4", 17))
        self.assertEqual((third["ssid"], third["band"], third["security"]), ("NETGEAR58", "2.4", "WPA2-Personal"))
        self.assertIsNone(fourth["ssid"])
        self.assertEqual(fourth["security"], "Open")


class LinuxTests(unittest.TestCase):
    def test_nmcli(self):
        link, neighbors = wifi.parse_nmcli(NMCLI)
        self.assertTrue(link["connected"])
        self.assertEqual((link["ssid"], link["bssid"], link["channel"], link["band"]), ("Maple House", "08:f0:1e:10:21:42", 44, "5"))
        self.assertEqual(len(neighbors), 3)
        self.assertEqual(neighbors[2]["security"], "Open")
        self.assertIsNone(neighbors[2]["ssid"])

    def test_iw_link(self):
        info = wifi.parse_iw_link(IW_LINK)
        self.assertEqual((info["bssid"], info["ssid"], info["channel"], info["band"]), ("08:f0:1e:10:21:42", "Maple House", 44, "5"))
        self.assertEqual((info["rssi"], info["tx_rate"], info["rx_rate"], info["width"], info["phy"]), (-57, 650, 866, 80, "802.11ac"))

    def test_iw_not_connected(self):
        self.assertEqual(wifi.parse_iw_link("Not connected.\n"), {})

    def test_proc_wireless(self):
        self.assertEqual(wifi.parse_proc_wireless(PROC_WIRELESS), {"wlp3s0": (-50, None)})


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.link = {"connected": True, "channel": 4, "band": "2.4", "width": 20}
        self.neighbors = [
            {"channel": 6, "band": "2.4", "rssi": -50}, {"channel": 6, "band": "2.4", "rssi": -60},
            {"channel": 1, "band": "2.4", "rssi": -80}, {"channel": 149, "band": "5", "rssi": -60},
            {"channel": 36, "band": "5", "rssi": -45, "mine": True}, {"channel": 44, "band": "5", "rssi": -92},
        ]

    def test_off_grid_channel_is_flagged(self):
        result = channels.analyze(self.link, self.neighbors)
        band = result["bands"]["2.4"]
        self.assertEqual(band["current"], 4)
        self.assertEqual(band["advice"], "move")
        self.assertEqual(band["recommended"], 11)
        self.assertEqual(len(band["scores"]), 11)

    def test_own_nodes_and_inaudible_networks_do_not_count(self):
        result = channels.analyze(self.link, self.neighbors)
        self.assertEqual(result["density"]["visible"], 4)
        self.assertEqual(result["mine"], 1)
        blocks = {b["name"]: b for b in result["bands"]["5"]["blocks"]}
        self.assertEqual(blocks["36-48"]["networks"], 0)
        self.assertEqual(blocks["149-161"]["networks"], 1)

    def test_quiet_channel_is_kept(self):
        link = {"connected": True, "channel": 11, "band": "2.4", "width": 20}
        band = channels.analyze(link, self.neighbors)["bands"]["2.4"]
        self.assertEqual((band["advice"], band["recommended"]), ("keep", 11))

    def test_crowded_five_ghz_block(self):
        link = {"connected": True, "channel": 44, "band": "5", "width": 80}
        neighbors = [{"channel": ch, "band": "5", "rssi": -55} for ch in (36, 40, 44, 48, 44)]
        band = channels.analyze(link, neighbors)["bands"]["5"]
        self.assertEqual((band["current_block"], band["advice"], band["recommended"]), ("36-48", "move", "149-161"))

    def test_survives_json_round_trip(self):
        result = json.loads(json.dumps(channels.analyze(self.link, self.neighbors)))
        self.assertEqual(result["bands"]["2.4"]["scores"][5]["channel"], 6)

    def test_density_levels(self):
        self.assertEqual(channels.density_level(3), "low")
        self.assertEqual(channels.density_level(12), "medium")
        self.assertEqual(channels.density_level(25), "high")
        self.assertEqual(channels.density_level(60), "very high")


if __name__ == "__main__":
    unittest.main()
