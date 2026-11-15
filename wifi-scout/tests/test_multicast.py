"""Live socket tests for device discovery, against small fake responders on this machine.

They need multicast to loop back on the local interface. Where it does not, they skip.
"""
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from wifiscout import discovery, dnsmsg, netinfo, router
from tests.test_discovery import IGD_XML, sample_response


def join(group, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", port))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(group) + socket.inet_aton("0.0.0.0"))
    sock.settimeout(0.2)
    return sock


class Responder(threading.Thread):
    def __init__(self, sock, reply):
        threading.Thread.__init__(self)
        self.daemon = True
        self.sock = sock
        self.reply = reply
        self.heard = []
        self.running = True

    def run(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            answer = self.reply(data, addr)
            self.heard.append(data)
            if answer:
                self.sock.sendto(answer, addr)

    def stop(self):
        self.running = False
        self.join(timeout=2)
        self.sock.close()


class MulticastTests(unittest.TestCase):
    def test_mdns_query_and_reply(self):
        try:
            sock = join(discovery.MDNS_GROUP, discovery.MDNS_PORT)
        except OSError as exc:
            self.skipTest("cannot join the mDNS group here: %s" % exc)

        def reply(data, addr):
            try:
                message = dnsmsg.parse_message(data)
            except ValueError:
                return None
            if message["response"] or addr[1] == discovery.MDNS_PORT:
                return None
            wanted = {q["name"] for q in message["questions"]}
            return sample_response() if "_airplay._tcp.local" in wanted else None

        responder = Responder(sock, reply)
        responder.start()
        try:
            found = discovery.mdns_scan(None, duration=1.5)
        finally:
            responder.stop()
        if not responder.heard:
            self.skipTest("multicast does not loop back on this machine")
        questions = set()
        for packet in responder.heard:
            try:
                questions.update(q["name"] for q in dnsmsg.parse_message(packet)["questions"])
            except ValueError:
                pass
        self.assertIn("_services._dns-sd._udp.local", questions)
        self.assertIn("_googlecast._tcp.local", questions)
        self.assertIn("192.168.4.30", found)
        self.assertEqual(found["192.168.4.30"]["services"], ["_airplay._tcp"])
        self.assertEqual(found["192.168.4.30"]["txt"]["model"], "AppleTV11,1")

    def test_ssdp_search_description_and_gateway_query(self):
        local_ip = netinfo.udp_local_ip()
        if not local_ip:
            self.skipTest("no network address")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = IGD_XML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                request = self.rfile.read(length).decode("utf-8")
                action = self.headers.get("SOAPAction", "")
                if "GetExternalIPAddress" in action and "GetExternalIPAddress" in request:
                    body = b"<s:Envelope><s:Body><u:R><NewExternalIPAddress>100.70.1.2</NewExternalIPAddress></u:R></s:Body></s:Envelope>"
                elif "GetStatusInfo" in action:
                    body = b"<s:Envelope><s:Body><u:R><NewConnectionStatus>Connected</NewConnectionStatus><NewUptime>86400</NewUptime></u:R></s:Body></s:Envelope>"
                else:
                    body = b"<s:Envelope><s:Body><u:R><NewLayer1DownstreamMaxBitRate>940000000</NewLayer1DownstreamMaxBitRate></u:R></s:Body></s:Envelope>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        web = HTTPServer((local_ip, 0), Handler)
        web_thread = threading.Thread(target=web.serve_forever)
        web_thread.daemon = True
        web_thread.start()
        try:
            sock = join(discovery.SSDP_GROUP, discovery.SSDP_PORT)
        except OSError as exc:
            web.shutdown()
            self.skipTest("cannot join the SSDP group here: %s" % exc)
        location = "http://%s:%d/rootDesc.xml" % (local_ip, web.server_address[1])

        def reply(data, addr):
            if not data.startswith(b"M-SEARCH"):
                return None
            return ("HTTP/1.1 200 OK\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
                    "SERVER: Linux UPnP/1.1 MiniUPnPd/2.2\r\nLOCATION: %s\r\n\r\n" % location).encode("ascii")

        responder = Responder(sock, reply)
        responder.start()
        try:
            found = discovery.ssdp_scan(None, duration=1.5)
        finally:
            responder.stop()
        try:
            if not responder.heard:
                self.skipTest("multicast does not loop back on this machine")
            self.assertIn(b'MAN: "ssdp:discover"', responder.heard[0])
            self.assertIn(local_ip, found)
            entry = found[local_ip]
            self.assertEqual(entry["description"]["model"], "RT-AX88U")
            info = router.igd_info(entry)
            self.assertEqual((info["wan_ip"], info["connection_status"], info["uptime_s"]), ("100.70.1.2", "Connected", 86400))
            self.assertEqual(info["link_down_bps"], 940000000)
        finally:
            web.shutdown()
            web.server_close()


if __name__ == "__main__":
    unittest.main()
