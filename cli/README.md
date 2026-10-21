# iHost Control

Small CLI to control **all devices on a SONOFF iHost** through eWeLink CUBE Open API V2.
Pure Python standard library: nothing to install.

## Setup (once)

1. The PC and the iHost must be on the same network.
2. Open a terminal in this folder and run:
   ```
   ihost token --host 192.168.1.50        (your iHost IP, or ihost.local)
   ```
3. While it says "waiting...", open `http://<iHost IP>` in a browser and click **Done** on the popup.
4. The token is saved to `config.json`. Keep it private: it gives full control of the gateway.

## Targets

| Target | Meaning |
|---|---|
| `all` | every device |
| `SN123...` | exact serial number, or its first 6+ characters (e.g. `234c492f`) |
| `kitchen` | name contains "kitchen" (case-insensitive) |
| `cat:light` | display category (`light`, `plug`, `switch`, `curtain`, ...) |
| `tag:room=living` | devices with that tag |
| `cat:light,tag:room=bedroom` | comma = combine several |

## Commands

```
ihost list                         all devices + live state
ihost list tag:room=living
ihost off all                      asks y/N first; -y skips, -n = dry run
ihost on kitchen
ihost toggle tag:room=living -y
ihost on "wall switch" -c 2        one channel of a multi-gang switch
ihost set cat:light brightness=40 colortemp=20     (0-100; 0 = warm)
ihost set curtain percentage=50
ihost raw SN123 "{\"percentage\":{\"percentage\":40}}"   any capability state
ihost tag SN123 room=living        make groups (room= removes the tag)
ihost rename SN123 "Kitchen Plug"
ihost scenes / ihost scene goodnight [-n]
ihost watch                        live events (on/off, sensors, online/offline)
ihost info                         gateway info
```

Offline devices are skipped unless you pass `--include-offline`. Sensors and other devices
without that capability are skipped automatically.

## Scenes

Copy `scenes.example.json` to `scenes.json` and edit it. Each step has a `target` plus one of:
- `"action": "on" | "off" | "toggle"` (optional `"channel": "2"`)
- `"set": {"brightness": 30, "colortemp": 0, "percentage": 100}`
- `"state": { ...raw Open API state... }`
- optional `"delay": seconds` after the step

## Other ways to set the host/token

Use the environment variables `IHOST_HOST` and `IHOST_TOKEN`. They override `config.json`.

## Testing without a gateway

```
python tests/mock_ihost.py 8765
set IHOST_HOST=http://127.0.0.1:8765
ihost token
ihost list
```
