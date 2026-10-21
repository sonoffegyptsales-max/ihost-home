# iHost Home

A HomeKit-style web app for SONOFF iHost hubs:

- See every switch's live status and turn things on and off.
- Rename switches and channels, group them into rooms, and pick favorites.
- Connect to **any iHost by its IP address**, and to several homes at once.
- Control everything **from home (LAN) or over the internet** with a secure link.

Works on phones (add to home screen), tablets and desktop. Light and dark mode follow the device.

No installs: Python 3.9+ standard library only. Nothing to build.

```
ihost-home/
  server.py          serves the UI, talks to the iHosts, fans out live events, runs the internet tunnel
  static/            the web app (HTML/CSS/vanilla JS, installable as a PWA)
  cli/               command-line tool for the same iHost
  tests/             fake iHost + browser tests
```

## Start

Double-click `start.bat`, or run:

```
python server.py
```

It prints the addresses to open:

- `http://localhost:8088`: on this computer
- `http://192.168.x.x:8088`: on phones and tablets on the same Wi-Fi

The first time, Windows Firewall may ask about Python. Allow **Private networks** or phones can't connect.

On iPhone, open the address in Safari, tap Share, then **Add to Home Screen**.

## Connect an iHost (by its internal IP)

1. Open the app. With no iHost yet it shows **Add iHost**. Otherwise tap the home name at the top, then **Add iHost…**
2. The app scans your network and lists every iHost it finds, with its IP. Tap one, or type the IP (e.g. `192.168.1.50`).
3. Give the home a name (e.g. *Villa · New Cairo*) and tap **Connect**.
4. Open the iHost's own web page (`http://<iHost IP>`) and click **Done** on the popup asking to allow *iHost Home*.

Repeat for more iHosts. Switch between homes by tapping the home name at the top.

- **If an iHost gets a new IP:** use ••• → **Change IP Address / Pair Again…**, or simply add it again. The same iHost is recognised by its device ID and updated, not duplicated.
- **Tip:** give each iHost a fixed IP (DHCP reservation) in the router.

Adding or removing iHosts and scanning only work from the home network, never over the internet.

## Control over the internet

The app creates a secure HTTPS link through Cloudflare Tunnel:

- No router changes or port forwarding.
- Nothing to install: the connector (`cloudflared`) downloads automatically the first time.

1. ••• → **Password**: set a password with at least 8 characters. It's required for every internet visitor.
   A short PIN (4+ digits) is fine for home-only use. Turning on Internet Access sends you here first if your PIN is too short.
2. ••• → **Internet Access**: turn it on. It shows *Getting your link ready…* until the new address works
   (about 10–30 seconds), then **Your Link** appears, e.g. `https://xxxx.trycloudflare.com`.
   Don't open the address before it appears: phones remember a failed lookup for a few minutes.
3. Open that link on your phone anywhere and sign in with the password.

Optional: **Sign-in Link** creates a link that signs a phone straight in without typing the password, for family members.
Creating a new link cancels the old one. **Sign Out All Other Devices** logs everyone out.

Two kinds of address:

| | Free Link (default) | Own Domain |
|---|---|---|
| Setup | none | free Cloudflare account + a domain |
| Address | random `*.trycloudflare.com`, **changes when the app restarts** | fixed, e.g. `https://home.example.com` |
| Updates | refresh every 3 seconds (Cloudflare's free links don't stream live events) | instant |
| Intended for | trying it out, occasional use | everyday use |

**Setting up your own domain:**
1. In Cloudflare Zero Trust, go to Networks → Tunnels → **Create a tunnel** (type *Cloudflared*), then copy the token.
2. Add a public hostname, e.g. `home.example.com`, with service `http://localhost:8088`.
3. In the app: ••• → Internet Access → **Own Domain**. Paste the token and the address, tap **Save**, then turn Internet Access on.

The computer running the app must stay on for internet access. While Internet Access is off, every request
from outside is refused, even with a valid password.

## Using it

| Action | How |
|---|---|
| Turn on/off | Tap a tile |
| Details, rename, room, favorite, icon | Long-press a tile (right-click on desktop) |
| Rename one channel of a multi-gang switch | Details, then **Name** (or the list under *Switches on This Device*) |
| Rename the whole device | Details, then **Device Name** |
| Move to a room | Details, then **Room**, then pick one or type a new room |
| Room off | **Turn Off** next to the room title |
| Everything off | Tap the **Lights & Switches** chip, then **Turn Off All** |
| Switch home / add iHost | Tap the home name at the top |
| Rooms, hub info, password, internet access | **•••** button (top right) |

The dot next to ••• shows how updates arrive:

- **Green:** live. Changes from wall buttons, the eWeLink app or automations appear within about a second.
- **Orange:** refreshing every 3 seconds (the free internet link).

## Security

- The iHost tokens stay on the computer running the app. Browsers only talk to this server.
- **At home**, the app opens without a password unless you turn on ••• → **Ask for Password at Home**.
- **From the internet:**
  - A password is always required.
  - Requests are recognised by Cloudflare's headers, which visitors can't remove.
  - Wrong passwords lock out an address after 5 tries (15 minutes), and all internet logins after 30 failures in an hour.
- Passwords are stored as PBKDF2-SHA256 hashes. Sessions are signed cookies (`HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS) that last 90 days.
- Changing the password or tapping **Sign Out All Other Devices** ends every other session.
- From the internet you can control devices and rename things. You can't turn Internet Access off, add or remove iHosts, or scan the network.
- Cross-site requests are blocked. Pages are sent with a strict Content-Security-Policy and can't be framed.
- A PIN (4+ characters) protects the app at home. Internet Access requires a password with 8+ characters.
  An old PIN from version 1 keeps working at home until you change it.

## Where things are stored

| What | Where |
|---|---|
| Device names | On the iHost (also shows in the iHost console and eWeLink) |
| Channel names | iHost tags `toggle` |
| Rooms | iHost tag `room` (also used by the CLI: `ihost off tag:room=Kitchen`) |
| iHosts (IP, token, name), password hash, internet settings | `config.json` (keep private, never commit) |
| Favorites and icons per home | `prefs.json` |
| Cloudflare connector | `bin/cloudflared(.exe)` (downloaded on first use) |

Set `IHOST_HOME_DATA=<folder>` to keep `config.json`, `prefs.json` and `bin/` somewhere else.
`port` (default 8088) and `bind` (`0.0.0.0` = LAN, `127.0.0.1` = this computer only) are in `config.json`.

## Command-line tool

`cli/ihost.py` controls an iHost from a terminal (list, on/off/toggle by name, room or category, scenes,
live event watch). See `cli/README.md`.

```
python cli/ihost.py token --host 192.168.1.50
python cli/ihost.py off tag:room=Kitchen
```

## Tests

Without a real iHost:

```
python tests/mock_ihost.py 8765          # fake iHost #1
python tests/mock_ihost.py 8766          # fake iHost #2
IHOST_HOME_DATA=./testdata python server.py   # then add both (127.0.0.1:8765 / :8766) in the app
python tests/ui_test.py http://127.0.0.1:8088 http://127.0.0.1:8765 ./shots
python tests/ui_test_access.py http://127.0.0.1:8088 <password> ./shots
```

- `tests/ui_test.py`: 24 headless-Chrome checks covering tiles, long-press, renaming, rooms, favorites, Turn Off All, live updates and layouts.
- `tests/ui_test_access.py`: 25 checks covering home switching, the network scan, the sign-in page, simulated and **real** internet access through the tunnel, and the sign-in link. It needs a password set and Internet Access on.
- `tests/ui_test_pin_internet.py`: 10 phone-style checks covering PIN change (including tapping outside the fields), the PIN-to-password upgrade, and turning on Internet Access, then signing in through the real public link.
  Run it against a fresh data folder whose `config.json` has `"pin": "1234"`.
- `tests/live_screens.py`: read-only screenshots against a running app.

Both browser suites need Chrome and `pip install websocket-client`.

## iHost API notes (firmware 2.14.1)

- `access_token` needs `?app_name=` (not in the docs).
- `GET /open-api/v2/rest/bridge` answers without a token. The network scan uses it to recognise iHosts.
- Tag updates are a shallow merge of top-level keys. A key can only be cleared by sending it as `null`.
