# Tuya Bridge for SONOFF iHost

Puts **Tuya Wi-Fi devices** (plugs, 1–8 gang switches, dimmable bulbs) **inside the iHost** as normal devices.
Once added they work in the iHost console, the ihost-home app (home and Vercel), and eWeLink CUBE scenes.

- Control is **local**: bridge → device on TCP 6668 with each device's `local_key`. No Tuya cloud during use.
- The Tuya cloud (free IoT developer project) is used **once**, only to read device IDs + local keys.
- Status is polled every 3 s; physical button presses show up in the iHost within a few seconds.
- If a device is unplugged it goes **offline** in the iHost; commands to it are refused (never faked).

## Install on the iHost (Docker add-on)

Needs a microSD card in the iHost (iHost console → **Docker** formats it).

1. iHost console → **Docker** → **Add** / custom image.
2. Image: `ghcr.io/sonoffegyptsales-max/ihost-tuya-bridge` (or `<dockerhub-user>/ihost-tuya-bridge`), tag `latest`.
3. **Network mode: host** (required: device discovery uses UDP broadcasts 6666/6667/7000).
4. Storage / volume: map a folder on the SD card to **`/data`** (keeps keys and settings across updates).
5. Optional environment variables:
   | Variable | Default | Meaning |
   |---|---|---|
   | `UI_PASSWORD` | *(none)* | Protect the setup page (user `admin`) |
   | `POLL_SECONDS` | `3` | How often devices are polled |
   | `PORT` | `8321` | Web page / control port |
   | `IHOST_HOST` | `http://127.0.0.1` | iHost address (only change when running elsewhere) |
6. Start it, then open **http://ihost.local:8321** (or `http://<iHost-IP>:8321`).

## Setup (on the page)

1. **Connect the iHost** → press Connect, then click **Done** on the iHost web console within 5 minutes.
2. **Import devices**: Tuya *Access ID / Access Secret* + data center (from iot.tuya.com → your cloud project),
   or paste the `devices.json` written by `python -m tinytuya wizard`.
3. **Scan network** → fills in each device's IP and protocol version.
4. **Test** a device, then **Add to iHost**.

If **Test** fails: check the IP, then try another protocol version in **Edit** (3.3 / 3.4 / 3.5) — a wrong
version looks exactly like a wrong key (no answer).

## Run elsewhere (PC, Raspberry Pi, NAS)

```
docker run -d --name tuya-bridge --network host --restart unless-stopped \
  -v tuya-bridge-data:/data -e IHOST_HOST=http://192.168.1.20 ghcr.io/sonoffegyptsales-max/ihost-tuya-bridge
```
or without Docker: `pip install tinytuya && python bridge.py` (data goes to `./data`).

## Tests

`tests/e2e_real_ihost.py <ihost-url> <token>` – runs the bridge with **simulated** Tuya devices against a real iHost:
adds 3 temporary devices, drives them both ways (iHost → device, button press → iHost), offline/online,
restart, then removes them.

Notes: the iHost firmware requires `header.version: "2"` on `/thirdparty/event` (the docs still say "1").
