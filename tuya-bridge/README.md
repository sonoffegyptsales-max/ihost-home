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

## IR air conditioners (Tuya IR blaster / "Smart IR")

- The **IR transmitter** itself is not added to the iHost (it has nothing to switch). The bridge only checks that it's
  online. It answers status checks with an empty reply, which counts as online.
- The **AC** (`infrared_ac`) is added as an iHost **airConditioner**: power, mode (cool/heat/auto/fan/dry), 16–30 °C, and
  fan (auto/low/medium/high). It shows up in the iHost console, iHost Home (LAN + Vercel) and CUBE scenes.
- Each command first uses a **learned IR code** (100% local) for that exact state: `off`, `cool_24`, `heat_22`, `fan`, `dry`…
  Learn codes on the bridge page → **IR codes**: set the real remote to the state, press **Learn**, then press a button on
  the remote while pointing it at the transmitter.
- With no learned code, the bridge uses Tuya's **cloud AC command** (`/v2.0/infrareds/{ir}/air-conditioners/{ac}/scenes/command`).
  That needs the Tuya cloud keys and an active IoT Core trial. Learn your common states to stay fully local.
- IR is one-way: the iHost shows the last command sent, not what the AC really does.

## Door locks

Tuya Wi-Fi locks have no local control. Everything goes through the Tuya cloud (keys from step 2).

**Status sensor in the iHost:** "Add status sensor to iHost" adds the lock as a **contact sensor**: closed = locked,
open = unlocked, plus battery. It shows in the iHost, iHost Home and CUBE scenes ("when the door is unlocked, turn on the
light"). It's checked every `LOCK_POLL_SECONDS` (default 300 s = 5 min, which keeps the free cloud plan's monthly calls
comfortable). 3 failed checks in a row mark it offline.

**Unlock switch in the iHost (optional, off by default):** "Allow…" (type `UNLOCK`) → "Add unlock switch to iHost"
adds a switch called "<lock> Unlock". Turning it on unlocks through the cloud, and it falls back to Off by itself. Turning it
off does nothing (Wi-Fi locks can't be locked remotely). Limited to 3 unlocks per 10 minutes, and every unlock is in the bridge log.
**Warning:** the iHost can't ask for a password, so anyone with access to the iHost, eWeLink app, CUBE scenes or the
iHost API token can open the door. "Disallow" removes the switch immediately. iHost Home never shows this switch and refuses
to flip it: the app keeps its own password-protected **Unlock Door** (see the main README).

`tests/e2e_lock.py <ihost-url> <token>`: 23 checks against a real iHost with a simulated Tuya cloud.

`tests/e2e_ac.py <ihost-url> <token>`: 20 checks for the AC path against a real iHost, using a simulated IR transmitter and cloud.
