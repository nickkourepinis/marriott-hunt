# WiFi Scout

Finds the gaps in a home or apartment Wi-Fi network and tells you how to close them.

It runs on a computer inside the home, uses your iPhone as the roaming probe, and writes one report:
a coverage map, a list of what is wrong, and a ranked list of fixes with steps.

Nothing to install beyond Python. No account. Everything stays on your computer.

## Start

You need a Mac, Windows PC or Linux computer on the home network, with Python 3.7 or newer.

```
cd wifi-scout
python3 -m wifiscout
```

On Windows use `py -m wifiscout`. On a Mac, the first time you type `python3` macOS offers to install it
(Command Line Tools). Accept, then run the command again.

That one command:

1. Scans the network (one to two minutes) and prints what it found.
2. Writes `wifiscout-data/report.html`.
3. Starts the phone survey and opens a page with a QR code.

Your computer may ask whether Python can accept incoming connections. Say yes, or the phone cannot reach it.

## Map the home with the iPhone

1. iPhone on the same Wi-Fi. Open Camera, point at the QR code.
2. Safari warns the connection is not private. That is expected: your own computer made the certificate.
   Tap **Show Details**, then **visit this website**.
3. Stand next to the router. Tap the map where you are. Tap **Measure here**. About 8 seconds.
4. Repeat in every room, the far corners, and anywhere Wi-Fi feels slow.
5. The page on the computer redraws the map as readings arrive. Press Ctrl-C when done.

Each reading measures response time and speed between the phone and your computer, inside your home.
That shows how good the Wi-Fi link is at that spot, separate from your internet speed.
Plug the computer into the router with a cable if you can: on Wi-Fi the speeds read low.

### Walk mode (step counter and compass)

Tap **Turn on** under Walk mode and allow motion access. The blue dot then follows you, so you do not
have to tap the map for each reading. Hold the phone flat, pointing where you walk. Tap **I am facing
map-up** to set the direction. If the dot drifts, tap your real position: it learns from the correction.
It needs the secure (https) address, which is the one in the QR code.

### Real signal strength (one-time Shortcut)

Web pages cannot read Wi-Fi signal strength on an iPhone. Apple's Shortcuts app can. Build this once:

1. Shortcuts app, **+**, name it exactly `WiFi Scout`.
2. Add **Get Network Details**. Tap *Network Name* and choose **RSSI**.
3. Add **Get Contents of URL**. Enter the address shown under **Setup** on the phone page. It ends with `&rssi=`.
4. With the cursor after the `=`, tap **Network Details** in the bar above the keyboard.
5. Run it once and tap Allow.

After a measurement, tap **Read it with the Shortcut**. The reading attaches to that spot.
For channel and access-point detail, add the same two actions again with *Channel Number* (`&ch=`),
*BSSID* (`&bssid=`), *TX Rate* (`&tx=`) and *Noise* (`&noise=`).

No Shortcut? Apple's free AirPort Utility shows the same number (enable Wi-Fi Scanner in
Settings > Apps > AirPort Utility). Type it into the reading.

Action names can differ slightly between iOS versions.

## Commands

Type `python3 -m wifiscout` wherever the table says `wifiscout`.

| Command | What it does |
|---|---|
| `wifiscout` | Full check, report, then the phone survey |
| `wifiscout scan` | Every device on the network: routers, mesh nodes, extenders, phones, TVs, smart-home gear |
| `wifiscout wifi` | This computer's signal, noise, channel, and how crowded each channel is |
| `wifiscout speed` | Internet speed, delay to the router and to the internet, DNS, lag under load |
| `wifiscout modem` | Cable modem signal levels, or 5G gateway signal |
| `wifiscout eero` | Mesh link quality and per-device signal from your eero account |
| `wifiscout survey` | Just the phone survey |
| `wifiscout monitor --hours 24` | Logs the connection to catch drop-outs |
| `wifiscout report --open` | Rebuilds the report from saved results |
| `wifiscout demo` | A sample report with invented data |

Useful options: `--plan 500/20` (your internet plan, download/upload in Mbps), `--home apartment`,
`--sqft 1100`, `--no-speed` (the speed test uses data), `--quick`, `--data FOLDER`, `--debug`.

## What it checks

- **Coverage.** Dead and weak zones per room and per floor, which access point served each spot,
  spots where signal is strong but speed is poor, phones clinging to a far access point.
- **Interference.** Neighbouring networks per channel on 2.4 and 5 GHz, off-grid channels,
  crowded-building detection, background noise.
- **Speed and delay.** Internet download and upload against your plan. Delay, jitter and loss to the
  router and to the internet separately, so you know which side a problem is on. DNS speed. Lag under load.
- **Devices.** Everything on the network, named and typed. Fixed devices with shaky links act as
  sensors for where coverage is thin.
- **Modem.** Downstream and upstream power, signal-to-noise, error counts, DOCSIS version.
- **Router.** Double NAT, carrier-grade NAT, second routers, extenders, mixed brands.
- **eero.** Each unit's link to the mesh, offline units, per-device signal, pending updates.
- **Stability.** Drop-outs over time, split into "inside the home" and "provider".

Each finding links to a fix: placement, settings, wiring (Ethernet, MoCA over coax, powerline),
or hardware (mesh nodes, outdoor access points, a newer router or modem), with cost and effort.

### How a spot is graded

| Verdict | Signal | Speed to your computer | Response time |
|---|---|---|---|
| Strong | -65 dBm or better | 75 Mbps or more | under 20 ms |
| Fair | -66 to -72 | 25 to 75 | 20 to 50 ms |
| Weak | -73 to -80 | 8 to 25 | 50 to 120 ms |
| Dead zone | below -80 | under 8 | over 120 ms |

The overall score blends whichever of the three were measured. One very bad value pulls it down.

## Modem behind a login

Many modems ask for a password before showing signal levels. Open `http://192.168.100.1` in a browser,
sign in (the password is often on the modem's label), save the connection or status page, then:

```
python3 -m wifiscout modem --file saved-page.html
```

## eero

eero has no local interface. `wifiscout eero` signs in with a code eero sends you and reads the network
through the same service the eero app uses. That service is not a published interface: it can change,
and this was written from public descriptions of it without an eero account to test against. It only
reads. The session is saved in `wifiscout-data/eero_session.json`; `wifiscout eero --logout` deletes it.

Without the account link, eero units are still found by the scan and mapped by the phone survey.

## Alerts

```
python3 -m wifiscout monitor --hours 24 --webhook https://maker.ifttt.com/trigger/wifi/json/with/key/YOUR_KEY
```

When the connection comes back after a drop, it posts the outage start and length to that address.
IFTTT Webhooks, Zapier or any address that accepts JSON works.

## Privacy and safety

- Results stay in `wifiscout-data/` on your computer.
- The survey page only answers devices on your local network that have the key in the QR code link.
  It runs only while the command is running. `--new-key` replaces the key.
- Traffic that leaves your network: the speed test (Cloudflare, or Apple's on a Mac), timing pings to
  1.1.1.1, 8.8.8.8 and 9.9.9.9, and, only if you use them, eero sign-in and your webhook.
- Your public address is masked in the report.
- Scan only networks you own or manage.

## Limits

- Tested with automated tests on recorded command output from macOS, Windows and Linux, and with an
  emulated iPhone browser. Not yet run on real Wi-Fi hardware, a real iPhone, a real modem or a real
  eero network. If a section is empty or wrong, run with `--debug` and look in `wifiscout-data/debug/`.
- macOS hides network names from command-line tools unless Location Services is allowed for the
  terminal app. Signal and channel data still work.
- Windows reports signal as a percentage, so dBm values there are estimates. Windows 11 needs
  Settings > Privacy & security > Location > "Let desktop apps access your location".
- Walk mode is dead reckoning from a step counter and compass. Expect a metre or two of drift per room.
- The bundled maker list is the IEEE registry from mid-2024. Newer devices may show no maker.
  Rebuild it with `python3 tools/build_oui.py oui.txt`.

## Trouble

| Problem | Try |
|---|---|
| Phone cannot open the page | Allow Python through the firewall. Phone on the same network, not a guest network. VPN off on both. |
| "Scan the QR code again" | The link carries a key. Scan the code on screen now. |
| The Shortcut stopped working | The computer's address changed. The phone page shows the current one under Setup. |
| Walk mode does nothing | Use the https address. Settings > Apps > Safari > Motion & Orientation Access on. |
| Few devices found | VPN off. Run again: sleeping devices answer late. |
| Speed test fails | `wifiscout speed` again later, or check speed.cloudflare.com in a browser. |

## Single file

`python3 tools/build_pyz.py` writes `dist/wifiscout.pyz`. Copy that one file anywhere and run
`python3 wifiscout.pyz`.

## Tests

```
python3 -m unittest discover -s tests -t .
node --test tests/js/common.test.js
```
