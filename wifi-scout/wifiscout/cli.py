"""Command line for WiFi Scout."""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
import time
import webbrowser

from . import __version__, audit, demo, eero, modem, monitor, netinfo, osutil, qr, report, server, store

SEVERITY_TAG = {"critical": "[!!]", "major": "[! ]", "minor": "[- ]", "info": "[i ]", "good": "[ok]"}


def file_link(path):
    """A file:// address that works on Windows as well."""
    import pathlib
    return pathlib.Path(path).resolve().as_uri()


def say(message=""):
    print(message)
    sys.stdout.flush()


def table(headers, rows):
    """Print a plain text table sized to the terminal."""
    width = shutil.get_terminal_size((100, 24)).columns
    rows = [[("" if cell is None else str(cell)) for cell in row] for row in rows]
    widths = [len(head) for head in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    budget = max(40, width - 2 * (len(headers) - 1))
    while sum(widths) > budget:
        widest = widths.index(max(widths))
        if widths[widest] <= 8:
            break
        widths[widest] -= 1
    line = "  ".join(head.ljust(widths[index]) for index, head in enumerate(headers))
    say(line.rstrip())
    say("  ".join("-" * widths[index] for index in range(len(headers))))
    for row in rows:
        say("  ".join((cell[:widths[index] - 1] + "~" if len(cell) > widths[index] else cell).ljust(widths[index])
                      for index, cell in enumerate(row)).rstrip())


def wrap(text, indent=6):
    width = max(40, shutil.get_terminal_size((100, 24)).columns - indent)
    words, line, lines = text.split(), "", []
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        lines.append(line)
    return ("\n" + " " * indent).join(lines)


def print_summary(bundle):
    analysis = bundle["analysis"]
    say()
    if analysis["score"] is not None:
        say("NETWORK HEALTH: %d out of 100" % analysis["score"])
        if analysis.get("score_note"):
            say("(%s)" % analysis["score_note"])
    say(wrap(analysis["verdict"], 0))
    problems = [f for f in analysis["findings"] if f["severity"] != "good"]
    good = [f for f in analysis["findings"] if f["severity"] == "good"]
    if problems:
        say()
        say("WHAT WAS FOUND")
        for finding in problems:
            say(" %s %s" % (SEVERITY_TAG[finding["severity"]], finding["title"]))
            if finding["detail"]:
                say("      " + wrap(finding["detail"]))
            for item in finding["evidence"][:4]:
                say("      - " + wrap(item, 8))
    if good:
        say()
        say("WORKING WELL")
        for finding in good:
            say(" %s %s" % (SEVERITY_TAG["good"], finding["title"]))
    if analysis["plan"]:
        say()
        say("WHAT TO DO, IN ORDER")
        for item in analysis["plan"][:8]:
            cost = "free" if item["cost"] == "free" else "cost " + item["cost"]
            say(" %d. %s  (%s, %s)" % (item["step"], item["title"], cost, item["effort"]))
            for note in item["notes"][:2]:
                say("      " + wrap(note))
    say()
    say(" [!!] critical   [! ] problem   [- ] could be better   [i ] note   [ok] good")


def print_devices(scan):
    devices = scan.get("devices") or []
    say("%d devices on %s" % (len(devices), (scan.get("network") or {}).get("network") or "the network"))
    rows = []
    for item in devices:
        link = "this computer" if item["self"] else (
            "" if item.get("loss_pct") is None else "%s ms%s" % (
                "?" if item.get("rtt_ms") is None else "%.0f" % item["rtt_ms"],
                ", %d%% loss" % item["loss_pct"] if item.get("loss_pct") else ""))
        rows.append([item["ip"], item.get("name"), item.get("label") if item.get("label") != item.get("name") else item.get("type"),
                     item.get("vendor") or ("private address" if item.get("random_mac") else ""), link])
    table(["Address", "Name", "What it is", "Maker", "Link"], rows)
    for note in scan.get("notes") or []:
        say("Note: " + note)


def print_wifi(section):
    link = section.get("link") or {}
    if link.get("connected"):
        say("Connected to %s" % (link.get("ssid") or "(name hidden by the operating system)"))
        say("  Band %s GHz, channel %s%s" % (link.get("band") or "?", link.get("channel") or "?",
                                             ", %s MHz wide" % link["width"] if link.get("width") else ""))
        say("  Signal %s dBm%s%s" % (link.get("rssi"), ", noise %s dBm" % link["noise"] if link.get("noise") is not None else "",
                                     ", signal-to-noise %s dB" % link["snr"] if link.get("snr") is not None else ""))
        say("  %s%s, link rate %s Mbps, security %s" % (link.get("generation") or "", " (%s)" % link["phy"] if link.get("phy") else "",
                                                        link.get("tx_rate") or "?", link.get("security") or "?"))
    else:
        say("This computer has no Wi-Fi link to report.")
    channels = section.get("channels") or {}
    density = channels.get("density") or {}
    if density:
        say("%d other networks in range (%d strong): %s crowding" % (density.get("visible", 0), density.get("strong", 0), density.get("level")))
    band24 = (channels.get("bands") or {}).get("2.4")
    if band24 and band24.get("scores"):
        say()
        say("2.4 GHz competition by channel (higher = busier):")
        top = max([row["score"] for row in band24["scores"]] + [1.0])
        for row in band24["scores"]:
            mark = " <- you" if band24.get("current") == row["channel"] else (
                " <- clearest" if band24.get("recommended") == row["channel"] and band24.get("advice") == "move" else "")
            say("  ch %2d  %-30s %4.1f%s" % (row["channel"], "#" * int(round(30 * row["score"] / top)), row["score"], mark))
    band5 = (channels.get("bands") or {}).get("5")
    if band5 and band5.get("blocks"):
        say()
        say("5 GHz competition by channel group:")
        for block in band5["blocks"]:
            mark = " <- you" if band5.get("current_block") == block["name"] else ""
            say("  %-8s %2d networks  %4.1f%s%s" % (block["name"], block["networks"], block["score"],
                                                    "  (radar-shared)" if block["dfs"] else "", mark))
    for note in section.get("notes") or []:
        say("Note: " + wrap(note, 6))


def print_modem(result):
    status = result.get("status")
    if status == "ok" and result.get("kind") == "cable":
        summary = result["summary"]
        say("Cable modem%s: %d downstream and %d upstream channels%s" % (
            " (%s)" % result["model"] if result.get("model") else "", summary["downstream_channels"],
            summary["upstream_channels"], ", DOCSIS %s" % summary["docsis"] if summary.get("docsis") else ""))
        say("  Downstream power %s to %s dBmV   (healthy: -10 to +10)" % (summary["down_power_min"], summary["down_power_max"]))
        say("  Signal-to-noise  %s dB lowest      (healthy: 33 or more)" % summary["snr_min"])
        say("  Upstream power   %s dBmV highest   (healthy: 35 to 49)" % summary["up_power_max"])
        say("  Uncorrectable errors: %s" % summary["uncorrected"])
    elif status == "ok":
        cell = result.get("cellular") or {}
        for key in ("5g", "4g"):
            if key in cell:
                say("%s signal: power %s dBm, quality %s dB, bands %s" % (key.upper(), cell[key]["rsrp"], cell[key]["sinr"],
                                                                          ", ".join(cell[key]["bands"]) or "?"))
    elif status == "login_required":
        say("The modem page at %s needs a login." % (result.get("url") or "http://192.168.100.1"))
        say("Sign in with a browser, save the status page, then run: %s modem --file saved-page.html" % osutil.command_name())
    elif status == "no_data":
        say("Something answered at %s, but no modem signal table was found there." % (result.get("address") or "192.168.100.1"))
        say("If you have a cable modem, open that address in a browser, save the page that lists the")
        say("downstream and upstream channels, and run: %s modem --file saved-page.html" % osutil.command_name())
    else:
        say("No modem status page found at 192.168.100.1. That is normal for fibre and for many all-in-one gateways.")


def bonjour_name():
    """This Mac's name on the local network, e.g. 'Study-MacBook.local'. None elsewhere."""
    if osutil.PLATFORM != "mac":
        return None
    name = osutil.run(["scutil", "--get", "LocalHostName"], timeout=5)[1].strip()
    if name and all(ch.isalnum() or ch == "-" for ch in name):
        return name + ".local"
    return None


def open_data(args):
    data = store.DataDir(args.data)
    if args.debug:
        osutil.set_debug_dir(data.path("debug"))
        say("Saving raw command output to %s" % data.path("debug"))
    return data


def apply_profile(data, args):
    """Store --home / --plan / --sqft so the analysis and the phone page see them."""
    updates = {}
    if getattr(args, "home", None):
        updates["home"] = args.home
    if getattr(args, "sqft", None):
        updates["sqft"] = args.sqft
    if getattr(args, "plan", None):
        parts = args.plan.replace(",", "/").split("/")
        try:
            updates["plan_down"] = float(parts[0])
            if len(parts) > 1:
                updates["plan_up"] = float(parts[1])
        except ValueError:
            say("Ignoring --plan %r: write it like 500/20 (download/upload in Mbps)." % args.plan)
    if updates:
        survey = store.Survey(data)
        profile = dict(survey.snapshot()["profile"])
        profile.update(updates)
        survey.set_profile(profile)


def save_audit_part(data, name, value):
    saved = data.load_json("audit.json", {}) or {}
    saved.setdefault("meta", {"version": __version__, "generated": time.time(), "platform": osutil.PLATFORM})
    saved["meta"]["generated"] = time.time()
    saved[name] = value
    data.save_json("audit.json", saved)


def serve(data, args, net=None):
    """Start the survey server, show how to reach it, and wait for Ctrl-C."""
    net = net or netinfo.get_network()
    if getattr(args, "new_key", False):
        server.load_key(data, renew=True)
    try:
        app, servers = server.start(data, local_ip=net.get("local_ip"), collector_via=net.get("via") or "unknown",
                                    http_port=args.port, https_port=args.port + 1, tls=not args.no_tls,
                                    stable_host=bonjour_name())
    except OSError as exc:
        say("Could not start the survey server: %s" % exc)
        return 1
    dashboard = "http://127.0.0.1:%d/?k=%s" % (app.http_port, app.token)
    phone = app.phone_url()
    say()
    say("PHONE SURVEY IS READY")
    say("  On this computer:  %s" % dashboard)
    say("  On your iPhone:    %s" % phone)
    if app.https_port is None:
        say("  (Secure mode could not start, so walk mode is unavailable. Tapping the map still works.)")
    else:
        say("  Safari will warn about the certificate, because this computer made it itself.")
        say("  Tap 'Show Details', then 'visit this website'.")
    if net.get("via") == "wifi":
        say("  Tip: this computer is on Wi-Fi. Plug it into the router with a cable for true speed readings.")
    if osutil.enable_ansi():
        try:
            say()
            say(qr.to_terminal(qr.encode(phone)))
            say("  Scan with the iPhone camera (same Wi-Fi network).")
        except ValueError:
            pass
    say()
    say("Leave this window open while you walk around. Press Ctrl-C when finished.")
    if not args.no_browser:
        try:
            webbrowser.open(dashboard)
        except Exception:
            pass
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    for item in servers:
        item.shutdown()
    path, bundle = report.write(data)
    say()
    say("Survey stopped. %d spots saved." % len(bundle["survey"]["spots"]))
    say("Report: %s" % path)
    return 0


# ---------------------------------------------------------------- commands

def cmd_run(args):
    data = open_data(args)
    apply_profile(data, args)
    say("WiFi Scout %s: checking this network. Takes one to two minutes." % __version__)
    result = audit.run_audit(say=lambda message: say("* " + message) if not message.startswith("  ") else say(message),
                             quick=args.quick, do_speed=not args.no_speed)
    data.save_json("audit.json", result)
    path, bundle = report.write(data)
    if result.get("scan"):
        say()
        print_devices(result["scan"])
    print_summary(bundle)
    say()
    say("Report: %s" % path)
    if args.no_survey:
        if not args.no_browser:
            try:
                webbrowser.open(file_link(path))
            except Exception:
                pass
        return 0
    return serve(data, args, net=result.get("network"))


def cmd_scan(args):
    data = open_data(args)
    from . import scan
    result = scan.run_scan(progress=lambda message: say("* " + message), probe_ports=not args.quick, ping_devices=not args.quick)
    save_audit_part(data, "scan", result)
    save_audit_part(data, "network", result["network"])
    say()
    print_devices(result)
    return 0


def cmd_wifi(args):
    data = open_data(args)
    from . import channels, wifi
    section = wifi.collect()
    section["channels"] = channels.analyze(section["link"], section["neighbors"])
    save_audit_part(data, "wifi", section)
    print_wifi(section)
    return 0


def cmd_speed(args):
    data = open_data(args)
    from . import speed
    net = netinfo.get_network()
    say("* Timing the router and the internet")
    latency = speed.latency_profile(net.get("gateway"))
    save_audit_part(data, "network", net)
    save_audit_part(data, "latency", latency)
    for label, key in (("Router  ", "gateway"), ("Internet", "internet")):
        stats = latency.get(key)
        if stats and stats.get("median") is not None:
            say("  %s %6.1f ms   jitter %5.1f ms   loss %4.1f%%" % (label, stats["median"], stats["jitter"] or 0, stats["loss_pct"] or 0))
        elif stats:
            say("  %s no reply" % label)
    say("* Timing DNS lookups")
    dns = speed.dns_profile(net.get("dns"))
    save_audit_part(data, "dns", dns)
    for row in dns["system"] + dns["public"]:
        say("  %-22s %-15s %s" % (row["label"], row["server"], "no answer" if row["avg_ms"] is None else "%.0f ms" % row["avg_ms"]))
    if not latency.get("online"):
        say("No internet connection: skipping the speed test.")
        return 1
    idle = (latency.get("anchors") or {}).get("1.1.1.1") or {}
    result = speed.run_speedtest(duration=args.seconds, idle_ms=idle.get("median"), progress=lambda message: say("* " + message))
    result.pop("_public_ip", None)
    result["via"] = net.get("via")
    save_audit_part(data, "speed", result)
    say()
    if result.get("down_mbps") is None and result.get("up_mbps") is None:
        reason = result["errors"][0] if result["errors"] else "unknown reason"
        say("The speed test could not run: %s." % reason.split(": ", 1)[-1])
        return 1
    say("Download %s Mbps   Upload %s Mbps   (computer on %s)" % (result.get("down_mbps"), result.get("up_mbps"),
                                                                 "Wi-Fi" if net.get("via") == "wifi" else net.get("via")))
    if result.get("bloat_grade"):
        say("Lag under load: grade %s (+%s ms when the line is busy)" % (result["bloat_grade"], result["bloat_ms"]))
    if result.get("isp"):
        say("Provider: %s" % result["isp"])
    if result.get("note"):
        say(result["note"])
    return 0


def cmd_modem(args):
    data = open_data(args)
    if args.file:
        try:
            result = modem.parse_file(args.file)
        except OSError as exc:
            say("Cannot read %s: %s" % (args.file, exc))
            return 1
    else:
        say("* Looking for the modem's signal page")
        result = modem.probe(netinfo.get_network().get("gateway"))
    save_audit_part(data, "modem", result)
    print_modem(result)
    return 0 if result.get("status") == "ok" else 1


def cmd_eero(args):
    data = open_data(args)
    client = eero.EeroClient(data.path("eero_session.json"))
    if args.logout:
        client.sign_out()
        say("Signed out of eero and deleted the saved session.")
        return 0
    try:
        if args.login or not client.signed_in():
            say("This reads your eero network through the same service the eero app uses.")
            say("It is not an official interface, makes no changes, and may stop working if eero changes it.")
            identifier = input("eero account email or phone number: ").strip()
            if not identifier:
                return 1
            client.start_login(identifier)
            code = getpass.getpass("Verification code eero just sent you: ").strip()
            client.finish_login(code)
            say("Signed in. The session is saved in %s" % data.path("eero_session.json"))
        say("* Reading the eero network")
        result = client.fetch()
    except eero.EeroError as exc:
        say("eero: %s" % exc)
        return 1
    data.save_json("eero.json", result)
    say()
    say("eero network: %s" % (result["network"].get("name") or ""))
    table(["eero", "Model", "Link", "Devices", "Status"], [[
        node["name"], node.get("model"),
        "gateway" if node["gateway"] else "wired" if node["wired"] else "wireless, %s of 5 bars" % node.get("mesh_bars"),
        node.get("clients"), node.get("status")] for node in result["nodes"]])
    weak = sorted((c for c in result["clients"] if c["connected"] and c["wireless"] and c.get("rssi") is not None),
                  key=lambda c: c["rssi"])[:10]
    if weak:
        say()
        say("Weakest wireless devices (each is a fixed signal sensor):")
        table(["Device", "dBm", "Band", "Connected to"], [[c["name"], c["rssi"], c.get("band"), c.get("node")] for c in weak])
    path, bundle = report.write(data)
    say()
    say("Report updated: %s" % path)
    return 0


def cmd_survey(args):
    data = open_data(args)
    apply_profile(data, args)
    return serve(data, args)


def cmd_monitor(args):
    data = open_data(args)
    minutes = args.hours * 60.0 if args.hours else args.minutes
    path = data.path("monitor.jsonl")
    span = "%.1f hours" % (minutes / 60.0) if minutes >= 90 else "%d minutes" % minutes if minutes >= 2 else "%d seconds" % (minutes * 60)
    say("Watching the connection for %s. Press Ctrl-C to stop early." % span)
    monitor.run(path, minutes=minutes, interval=args.interval, webhook=args.webhook, progress=say)
    summary = monitor.summarize(monitor.load_samples(path))
    say()
    say("Online %.2f%% of %s minutes. %d drop-outs (%d inside the home, %d at the modem or provider)." % (
        summary["uptime_pct"] or 0, summary["duration_min"], len(summary["outages"]), summary["local_outages"],
        summary["upstream_outages"]))
    report_path, _ = report.write(data)
    say("Report updated: %s" % report_path)
    return 0


def cmd_report(args):
    data = open_data(args)
    apply_profile(data, args)
    path, bundle = report.write(data)
    print_summary(bundle)
    say()
    say("Report: %s" % path)
    if args.open:
        try:
            webbrowser.open(file_link(path))
        except Exception:
            pass
    return 0


def cmd_demo(args):
    data = store.DataDir(os.path.join(args.data, "demo"))
    demo.write(data)
    path, bundle = report.write(data)
    say("Sample report (invented data, not your network).")
    print_summary(bundle)
    say()
    say("Report: %s" % path)
    if args.serve:
        return serve(data, args, net={"local_ip": netinfo.udp_local_ip() or "127.0.0.1", "via": "ethernet"})
    if not args.no_browser:
        try:
            webbrowser.open(file_link(path))
        except Exception:
            pass
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="wifiscout",
        description="Find and fix gaps in home Wi-Fi. Run it with no command for the full check plus the iPhone survey.")
    parser.add_argument("--version", action="version", version="wifiscout " + __version__)
    parser.add_argument("--data", default="wifiscout-data", help="folder for results (default: ./wifiscout-data)")
    parser.add_argument("--debug", action="store_true", help="save the raw output of every system command")

    def add_serve_options(sub):
        sub.add_argument("--port", type=int, default=8787, help="port for the survey page (default 8787; secure page uses the next one)")
        sub.add_argument("--no-tls", action="store_true", help="do not start the secure page (disables walk mode)")
        sub.add_argument("--no-browser", action="store_true", help="do not open a browser window")
        sub.add_argument("--new-key", action="store_true", help="make a new access key (old phone links and the Shortcut address stop working)")

    def add_profile_options(sub):
        sub.add_argument("--home", choices=["house", "townhouse", "apartment", "condo"], help="type of home")
        sub.add_argument("--sqft", type=float, help="size of the home in square feet")
        sub.add_argument("--plan", metavar="DOWN/UP", help="your internet plan in Mbps, for example 500/20")

    commands = parser.add_subparsers(dest="command", metavar="command")
    run = commands.add_parser("run", help="full check, report, then the iPhone survey (the default)")
    run.add_argument("--quick", action="store_true", help="skip the speed test and per-device checks")
    run.add_argument("--no-speed", action="store_true", help="skip the internet speed test (it uses data)")
    run.add_argument("--no-survey", action="store_true", help="stop after the report")
    add_serve_options(run)
    add_profile_options(run)
    run.set_defaults(function=cmd_run)

    scan = commands.add_parser("scan", help="list every device on the network")
    scan.add_argument("--quick", action="store_true", help="skip port and link checks")
    scan.set_defaults(function=cmd_scan)
    commands.add_parser("wifi", help="this computer's Wi-Fi link and channel congestion").set_defaults(function=cmd_wifi)
    speed = commands.add_parser("speed", help="internet speed, delay, DNS and lag under load")
    speed.add_argument("--seconds", type=float, default=8.0, help="seconds per direction (default 8)")
    speed.set_defaults(function=cmd_speed)
    modem_cmd = commands.add_parser("modem", help="cable modem or 5G gateway signal levels")
    modem_cmd.add_argument("--file", help="a status page you saved from the browser")
    modem_cmd.set_defaults(function=cmd_modem)
    eero_cmd = commands.add_parser("eero", help="mesh health and per-device signal from your eero account")
    eero_cmd.add_argument("--login", action="store_true", help="sign in again")
    eero_cmd.add_argument("--logout", action="store_true", help="delete the saved eero session")
    eero_cmd.set_defaults(function=cmd_eero)
    survey = commands.add_parser("survey", help="start the iPhone room-by-room survey")
    add_serve_options(survey)
    add_profile_options(survey)
    survey.set_defaults(function=cmd_survey)
    mon = commands.add_parser("monitor", help="log the connection over time to catch drop-outs")
    mon.add_argument("--minutes", type=float, default=60.0)
    mon.add_argument("--hours", type=float, default=0.0)
    mon.add_argument("--interval", type=float, default=10.0, help="seconds between checks (default 10)")
    mon.add_argument("--webhook", help="address to notify when the connection returns after a drop (IFTTT, Zapier...)")
    mon.set_defaults(function=cmd_monitor)
    rep = commands.add_parser("report", help="rebuild the report from saved results")
    rep.add_argument("--open", action="store_true", help="open it in the browser")
    add_profile_options(rep)
    rep.set_defaults(function=cmd_report)
    dem = commands.add_parser("demo", help="show a sample report without touching the network")
    dem.add_argument("--serve", action="store_true", help="also start the survey page on the sample data")
    add_serve_options(dem)
    dem.set_defaults(function=cmd_demo)
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"run", "scan", "wifi", "speed", "modem", "eero", "survey", "monitor", "report", "demo"}
    if not any(arg in known for arg in argv) and not any(arg in ("-h", "--help", "--version") for arg in argv):
        argv.append("run")
    args = parser.parse_args(argv)
    try:
        return args.function(args) or 0
    except KeyboardInterrupt:
        say()
        say("Stopped.")
        return 130
