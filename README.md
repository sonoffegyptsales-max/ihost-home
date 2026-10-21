# iHost Home

A HomeKit-style web app for your SONOFF iHost: see every switch's live status, turn things on and off,
rename switches and channels, group them into rooms, and pick favorites. Works on phones (add to
home screen), tablets and desktop. Light and dark mode follow the device.

No installs: Python 3.9+ standard library only. Nothing to build.

```
ihost-home/
  server.py          local server: serves the UI, proxies the iHost API, fans out live events
  static/            the web app (HTML/CSS/vanilla JS, installable as a PWA)
  cli/               command-line tool for the same iHost
  tests/             fake iHost + browser tests
```

## Start

Double-click `start.bat`, or run:

```
python server.py
```

It prints two addresses:

- `http://localhost:8088`: on this PC
- `http://192.168.x.x:8088`: on your phone (same Wi-Fi)

The first time, Windows Firewall may ask about Python. Allow **Private networks** or phones can't connect.

On iPhone, open the address in Safari, tap Share, then **Add to Home Screen**. It opens full-screen like an app.

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
| Rename/remove rooms, hub info, PIN | **•••** button (top right) |

Live updates: changes made from the eWeLink app, wall buttons or automations appear within about a second.
The green dot means the live connection is up.

## Where things are stored

| What | Where |
|---|---|
| Device names | On the iHost (shows in the iHost console and eWeLink) |
| Channel names | iHost tags `toggle` (documented iHost mechanism) |
| Rooms | iHost tag `room` (also used by `ihost-control`: `ihost off tag:room=Kitchen`) |
| Favorites, icons, home name | `prefs.json` here |
| iHost address + token, PIN | `config.json` here (keep private) |

## Security

- The iHost token stays on this PC. Browsers only talk to this server.
- Set an **Access PIN** in ••• → Security so other people on the Wi-Fi can't control the house.
  Wrong PINs are rate-limited (5 tries per 5 minutes).
- Cross-site requests are blocked.
- Don't expose port 8088 to the internet. Use a VPN (e.g. Tailscale) for remote access.

## Settings (`config.json`)

`host`, `token` (set by pairing), `port` (default 8088), `bind` (`0.0.0.0` = reachable on the LAN,
`127.0.0.1` = this PC only), `pin`.

To pair a different iHost: ••• → **Pair Again…**, then click **Done** in the iHost web console.

## Command-line tool

`cli/ihost.py` controls the same iHost from a terminal (list, on/off/toggle by name, room or category,
scenes, live event watch). See `cli/README.md`.

```
python cli/ihost.py token --host 192.168.1.50
python cli/ihost.py off tag:room=Kitchen
```

## Tests

Without a real iHost:

```
python tests/mock_ihost.py 8765                     # fake iHost with sample devices
# config.json: {"host": "http://127.0.0.1:8765", "token": "test-token-123", "port": 8099}
python server.py
python tests/ui_test.py http://127.0.0.1:8099 http://127.0.0.1:8765 ./shots
```

- `tests/ui_test.py`: 24 headless-Chrome checks (needs Chrome and `pip install websocket-client`).
- `tests/live_screens.py`: read-only screenshots against the running app.

## iHost API notes (firmware 2.14.1)

- `access_token` needs `?app_name=` (not in the docs).
- Tag updates are a shallow merge of top-level keys. A key can only be cleared by sending it as `null`.
