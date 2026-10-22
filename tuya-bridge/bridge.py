#!/usr/bin/env python3
"""iHost Tuya Bridge - puts Tuya Wi-Fi devices into a SONOFF iHost, controlled 100% locally.

Runs as a Docker add-on on the iHost itself (network mode: host), or on any always-on computer
on the same network. It:
  * pairs with the iHost (Open API V2 token),
  * imports Tuya devices + local keys (devices.json from `tinytuya wizard`, or the Tuya cloud API once),
  * finds their IP + protocol version on the LAN (UDP 6666/6667/7000 broadcasts),
  * registers them on the iHost as third-party devices (switch / plug / light, multi-gang aware),
  * executes the iHost's on/off/brightness commands over the local Tuya protocol (TCP 6668),
  * polls each device and reports changes back to the iHost.

Web page:  http://<host>:8321     Data: $DATA_DIR (default ./data, /data in Docker)
"""
import concurrent.futures
import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
DATA = os.environ.get("DATA_DIR") or os.path.join(HERE, "data")
CONFIG = os.path.join(DATA, "config.json")
PORT = int(os.environ.get("PORT") or 8321)
POLL_SECONDS = float(os.environ.get("POLL_SECONDS") or 3)
APP_NAME = "Tuya Bridge"
API = "/open-api/v2/rest"
FAKE = os.environ.get("BRIDGE_FAKE_TUYA") == "1"  # test mode: simulated Tuya devices, no network

lock = threading.RLock()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ config
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_config():
    cfg = {"ihost": {"host": os.environ.get("IHOST_HOST") or "http://127.0.0.1", "token": ""},
           "service_base": os.environ.get("SERVICE_BASE") or "",
           "hook_secret": "", "tuya": {"region": "eu", "api_key": "", "api_secret": "", "device_id": ""},
           "devices": {}}
    raw = _read(CONFIG, {})
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    if os.environ.get("IHOST_HOST"):
        cfg["ihost"]["host"] = os.environ["IHOST_HOST"]
    cfg["ihost"]["host"] = str(cfg["ihost"]["host"]).strip()
    cfg["ihost"]["token"] = str(cfg["ihost"]["token"] or "").strip()
    if not cfg["hook_secret"]:
        cfg["hook_secret"] = secrets.token_urlsafe(18)
    return cfg


CFG = load_config()


def save():
    with lock:
        os.makedirs(DATA, exist_ok=True)
        tmp = CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(CFG, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG)


save()


# ------------------------------------------------------------------ iHost client
class IHostError(Exception):
    pass


def ihost_base():
    h = CFG["ihost"]["host"].strip().rstrip("/")
    return h if h.startswith("http") else "http://" + h


def ihost(method, path, body=None, auth=True, timeout=8):
    req = urllib.request.Request(ihost_base() + API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", "Bearer " + CFG["ihost"]["token"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
    except (urllib.error.URLError, OSError) as e:
        raise IHostError(f"iHost unreachable at {ihost_base()} ({getattr(e, 'reason', e)})")
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise IHostError("iHost sent an invalid reply")


def ihost_event(name, payload, endpoint=None):
    ev = {"header": {"name": name, "message_id": str(uuid.uuid4()), "version": "2"}, "payload": payload}
    if endpoint:
        ev["endpoint"] = endpoint
    res = ihost("POST", "/thirdparty/event", {"event": ev})
    if (res.get("header") or {}).get("name") == "ErrorResponse" or ("error" in res and res.get("error") not in (0, None)):
        raise IHostError(f"iHost refused {name}: {json.dumps(res.get('payload') or res)[:300]}")
    return res


def service_base():
    """Base URL the iHost uses to reach us. On the iHost itself (host network) that's 127.0.0.1."""
    if CFG.get("service_base"):
        return CFG["service_base"].rstrip("/")
    host = urllib.parse.urlparse(ihost_base()).hostname or "127.0.0.1"
    if host in ("127.0.0.1", "localhost"):
        return f"http://127.0.0.1:{PORT}"
    try:  # the local address that routes to the iHost
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((socket.gethostbyname(host), 80))
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:{PORT}"
    except OSError:
        return f"http://127.0.0.1:{PORT}"


def service_address():
    return f"{service_base()}/ihost/{CFG['hook_secret']}/directive"


PAIR = {"state": "idle", "message": ""}


def pair_worker():
    PAIR.update(state="waiting", message="Open the iHost web console and click Done / Allow")
    deadline = time.time() + 300
    while time.time() < deadline and PAIR["state"] == "waiting":
        try:
            res = ihost("GET", "/bridge/access_token?app_name=" + urllib.parse.quote(APP_NAME), auth=False)
        except IHostError as e:
            PAIR.update(state="error", message=str(e))
            return
        if res.get("error") == 0 and (res.get("data") or {}).get("token"):
            CFG["ihost"]["token"] = res["data"]["token"]
            save()
            PAIR.update(state="done", message="Connected to the iHost")
            threading.Thread(target=resync_all, daemon=True).start()
            return
        time.sleep(2.5)
    if PAIR["state"] == "waiting":
        PAIR.update(state="timeout", message="Timed out - Done was not clicked within 5 minutes")


# ------------------------------------------------------------------ Tuya device model
def _values(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v or {}


LIGHT_CATEGORIES = {"dj", "dd", "dc", "fwd", "xdd", "fsd", "tgq", "tgkg", "gyd", "sxd", "tyndj"}
PLUG_CATEGORIES = {"cz", "pc", "wkcz"}
IR_HUB_CATEGORIES = {"wnykq", "hwktwkq", "wsdykq"}            # universal IR / IR+RF remotes
LOCK_CATEGORIES = {"ms", "jtmspro", "jtmsbh", "videolock", "gyms", "mk", "photolock", "bxx", "wf_ms"}
AC_DEFAULT = {"power": "off", "mode": "cool", "temp": 24, "fan": "auto"}
AC_MODES = ["cool", "heat", "auto", "fan", "dry"]          # index = Tuya cloud "mode"
AC_FANS = ["auto", "low", "medium", "high"]                 # index = Tuya cloud "wind"


def special_kind(dev):
    cat = (dev.get("category") or "").lower()
    if cat in IR_HUB_CATEGORIES:
        return "ir_hub"
    if cat == "infrared_ac":
        return "ac"
    if cat.startswith("infrared_"):
        return "ir_other"
    if cat in LOCK_CATEGORIES or "lock" in cat:
        return "lock"
    return None


def describe(dev):
    """Work out how a Tuya device maps to iHost: kind, display category, channels and DPS numbers."""
    special = special_kind(dev)
    if special:
        return {"display": "airConditioner" if special == "ac" else None, "single": None, "channels": [],
                "bright": None, "special": special}
    mapping = dev.get("mapping") or {}
    codes = {str(v.get("code")): str(k) for k, v in mapping.items() if isinstance(v, dict) and v.get("code")}
    cat = (dev.get("category") or "").lower()
    channels = []
    for i in range(1, 9):
        if f"switch_{i}" in codes:
            channels.append({"ch": str(i), "dps": codes[f"switch_{i}"]})
    single = None
    if len(channels) <= 1:
        single = codes.get("switch") or codes.get("switch_led") or codes.get("switch_1")
    if len(channels) == 1:
        single, channels = channels[0]["dps"], []
    # lights: switch_led + bright_value(_v2)
    bright = None
    for code in ("bright_value_v2", "bright_value"):
        if code in codes:
            vals = _values(mapping[codes[code]].get("values"))
            bright = {"dps": codes[code], "min": int(vals.get("min", 10 if code.endswith("v2") else 25)),
                      "max": int(vals.get("max", 1000 if code.endswith("v2") else 255))}
            break
    if cat in LIGHT_CATEGORIES or ("switch_led" in codes and not channels):
        display = "light"
        single = codes.get("switch_led") or single
    elif cat in PLUG_CATEGORIES:
        display = "plug"
    else:
        display = "switch"
    if not channels and not single:
        # no cloud mapping (e.g. hand-written devices.json): Tuya's usual layout is DPS 1 = main switch
        manual = dev.get("dps_channels")
        if manual:
            channels = [{"ch": str(i + 1), "dps": str(d)} for i, d in enumerate(manual)] if len(manual) > 1 else []
            single = str(manual[0]) if len(manual) == 1 else None
        else:
            single = "1"
    return {"display": display, "single": single, "channels": channels, "bright": bright if display == "light" else None,
            "special": None}


def ac_state(dev):
    return dict(AC_DEFAULT, **(dev.get("ac") or {}))


def ac_ihost_state(s):
    return {"power": {"powerState": s["power"]},
            "mode": {"airConditionerMode": {"modeValue": s["mode"]}, "fanLevel": {"modeValue": s["fan"]}},
            "thermostat-target-setpoint": {"manual-mode": {"targetSetpoint": s["temp"]}}}


def ac_endpoint(dev):
    return {
        "third_serial_number": "tuya_" + dev["id"],
        "name": dev.get("name") or "Air Conditioner",
        "display_category": "airConditioner",
        "capabilities": [
            {"capability": "power", "permission": "1100"},
            {"capability": "mode", "name": "airConditionerMode", "permission": "1100",
             "settings": {"supportedValues": {"type": "enum", "permission": "01", "values": AC_MODES}}},
            {"capability": "mode", "name": "fanLevel", "permission": "1100",
             "settings": {"supportedValues": {"type": "enum", "permission": "01", "values": AC_FANS}}},
            {"capability": "thermostat-target-setpoint", "name": "manual-mode", "permission": "1100",
             "settings": {"temperatureUnit": {"type": "enum", "permission": "01", "value": "c", "values": ["c"]},
                          "temperatureRange": {"type": "numeric", "permission": "01", "min": 16, "max": 30, "step": 1}}},
        ],
        "state": ac_ihost_state(ac_state(dev)),
        "tags": {"source": "tuya-bridge", "tuya_category": "infrared_ac"},
        "manufacturer": "Tuya",
        "model": "IR air conditioner",
        "firmware_version": "",
        "service_address": service_address(),
    }


def endpoint_for(dev):
    d = describe(dev)
    if d["special"] == "ac":
        return ac_endpoint(dev)
    caps, state = [{"capability": "power", "permission": "1100"}], {}
    st = dev.get("last_dps") or {}
    if d["channels"]:
        caps += [{"capability": "toggle", "permission": "1100", "name": c["ch"]} for c in d["channels"]]
        toggles = {c["ch"]: {"toggleState": "on" if st.get(c["dps"]) else "off"} for c in d["channels"]}
        state = {"toggle": toggles, "power": {"powerState": "on" if any(v["toggleState"] == "on" for v in toggles.values()) else "off"}}
    else:
        state = {"power": {"powerState": "on" if st.get(d["single"]) else "off"}}
    if d["bright"]:
        caps.append({"capability": "brightness", "permission": "1100"})
        state["brightness"] = {"brightness": to_pct(st.get(d["bright"]["dps"]), d["bright"])}
    return {
        "third_serial_number": "tuya_" + dev["id"],
        "name": dev.get("name") or "Tuya device",
        "display_category": d["display"],
        "capabilities": caps,
        "state": state,
        "tags": {"source": "tuya-bridge", "tuya_category": dev.get("category") or ""},
        "manufacturer": "Tuya",
        "model": (dev.get("product_name") or dev.get("model") or "Tuya device")[:60],
        "firmware_version": str(dev.get("version") or ""),
        "service_address": service_address(),
    }


def to_pct(raw, b):
    try:
        raw = int(raw)
    except (TypeError, ValueError):
        return 100
    return max(1, min(100, round((raw - b["min"]) * 100 / max(1, b["max"] - b["min"]))))


def from_pct(pct, b):
    pct = max(1, min(100, int(pct)))
    return int(round(b["min"] + (b["max"] - b["min"]) * pct / 100))


def ihost_state_from_dps(dev, dps):
    d = describe(dev)
    if d["channels"]:
        toggles = {c["ch"]: {"toggleState": "on" if dps.get(c["dps"]) else "off"} for c in d["channels"]}
        out = {"toggle": toggles, "power": {"powerState": "on" if any(v["toggleState"] == "on" for v in toggles.values()) else "off"}}
    else:
        out = {"power": {"powerState": "on" if dps.get(d["single"]) else "off"}}
    if d["bright"] and d["bright"]["dps"] in dps:
        out["brightness"] = {"brightness": to_pct(dps.get(d["bright"]["dps"]), d["bright"])}
    return out


# ------------------------------------------------------------------ Tuya local transport
class FakeTuya:
    """Simulated device for tests (BRIDGE_FAKE_TUYA=1). State is shared through a JSON file so a test can
    flip a 'physical' button from outside."""
    store = os.path.join(DATA, "fake_tuya.json")

    def __init__(self, dev):
        self.id = dev["id"]

    def _all(self):
        return _read(self.store, {})

    def status(self):
        st = self._all().get(self.id)
        if st is None or st.get("_offline"):
            return {"Error": "Network Error: Device Unreachable", "Err": "905"}
        return {"dps": {k: v for k, v in st.items() if not k.startswith("_")}}

    def set_multiple_values(self, data):
        with lock:
            allst = self._all()
            st = allst.get(self.id)
            if st is None or st.get("_offline"):
                return {"Error": "Network Error: Device Unreachable", "Err": "905"}
            st.update({str(k): v for k, v in data.items()})
            with open(self.store, "w", encoding="utf-8") as f:
                json.dump(allst, f)
        return {"dps": dict(data)}

    def close(self):
        pass


class Link:
    """One device connection, serialised with a lock (tinytuya sockets are not thread-safe)."""

    def __init__(self, dev):
        self.lock = threading.Lock()
        self.sig = None
        self.dev = None
        self.refresh(dev)

    def refresh(self, dev):
        sig = (dev.get("ip"), dev.get("key"), str(dev.get("version")))
        if sig == self.sig and self.dev:
            return
        self.sig = sig
        if FAKE:
            self.dev = FakeTuya(dev)
            return
        import tinytuya  # imported lazily so the web page works even if the module is missing
        d = tinytuya.Device(dev["id"], address=dev.get("ip") or None, local_key=dev.get("key") or "",
                            version=float(dev.get("version") or 3.3), connection_timeout=2,
                            connection_retry_limit=1, connection_retry_delay=0)
        d.set_socketTimeout(2)
        d.set_socketRetryLimit(1)
        self.dev = d

    def status(self):
        with self.lock:
            return self.dev.status()

    def set_dps(self, values):
        with self.lock:
            return self.dev.set_multiple_values(values)


LINKS = {}


def link_for(dev):
    with lock:
        lk = LINKS.get(dev["id"])
        if lk is None:
            lk = LINKS[dev["id"]] = Link(dev)
        else:
            lk.refresh(dev)
        return lk


def tuya_ok(res):
    return isinstance(res, dict) and not res.get("Error") and not res.get("Err")


def reachable(res):
    """IR hubs have no status of their own: they answer status() with an empty payload (None in tinytuya).
    Only an explicit error (e.g. 905 Device Unreachable) means the device is really gone."""
    return res is None or (isinstance(res, dict) and not res.get("Error") and not res.get("Err"))


# ------------------------------------------------------------------ IR transmitter (Tuya universal remote)
def _fake_store():
    return os.path.join(DATA, "fake_tuya.json")


class FakeIR:
    """Test double: sent codes are appended to fake_tuya.json[hub]['_sent']; a test 'presses a remote
    button' by writing fake_tuya.json[hub]['_learn_next']."""

    def __init__(self, hub):
        self.id = hub["id"]

    def _rw(self, fn):
        with lock:
            allst = _read(_fake_store(), {})
            st = allst.setdefault(self.id, {})
            out = fn(st)
            with open(_fake_store(), "w", encoding="utf-8") as f:
                json.dump(allst, f)
            return out

    def send_button(self, code):
        def f(st):
            if st.get("_offline"):
                raise IHostError("IR transmitter unreachable")
            st.setdefault("_sent", []).append(code)
        self._rw(f)

    def receive_button(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            code = self._rw(lambda st: st.pop("_learn_next", None))
            if code:
                return code
            time.sleep(0.3)
        return None


class IRLink:
    """Sends / learns raw IR codes through a Tuya IR hub. Shares the hub's socket lock with its status Link,
    because many Tuya devices only accept one local connection at a time."""

    def __init__(self, hub):
        self.hub = hub
        self.dev = None

    def _device(self):
        if FAKE:
            return FakeIR(self.hub)
        if self.dev is None:
            from tinytuya.Contrib.IRRemoteControlDevice import IRRemoteControlDevice
            ct = int(self.hub.get("ir_control_type") or 0)
            d = IRRemoteControlDevice(self.hub["id"], address=self.hub.get("ip"), local_key=self.hub.get("key") or "",
                                      version=float(self.hub.get("version") or 3.3), control_type=ct or None,
                                      connection_timeout=3, connection_retry_limit=1, connection_retry_delay=0)
            if not ct and d.control_type:
                self.hub["ir_control_type"] = d.control_type
                save()
            if not d.control_type:
                raise IHostError("Couldn't talk to the IR transmitter (no answer on the network)")
            self.dev = d
        return self.dev

    def send(self, code):
        with link_for(self.hub).lock:
            self._device().send_button(code)

    def learn(self, timeout=15):
        with link_for(self.hub).lock:
            res = self._device().receive_button(timeout)
        if isinstance(res, str) and len(res) > 8:
            return res
        return None


IR_LINKS = {}
DETECTING = set()


def ir_link(hub):
    with lock:
        lk = IR_LINKS.get(hub["id"])
        if lk is None or lk.hub is not hub:
            lk = IR_LINKS[hub["id"]] = IRLink(hub)
        return lk


def hub_for(dev):
    """The IR transmitter an IR remote (e.g. the AC) belongs to."""
    hubs = [d for d in CFG["devices"].values() if special_kind(d) == "ir_hub"]
    for key in ("ir_hub", "gateway_id", "parent"):
        hid = dev.get(key)
        if hid and hid in CFG["devices"]:
            return CFG["devices"][hid]
    return hubs[0] if len(hubs) == 1 else None


# ------------------------------------------------------------------ Tuya cloud (optional fallback for IR AC)
class CloudError(IHostError):
    pass


_cloud = {"obj": None, "sig": None}


def cloud_ready():
    t = CFG["tuya"]
    return bool(t.get("api_key") and t.get("api_secret"))


def cloud_call(url, post=None, action=None, _retry=True):
    t = CFG["tuya"]
    if not cloud_ready():
        raise CloudError("Tuya cloud keys not set (step 2 on this page)")
    if FAKE:
        def f(allst):
            allst.setdefault("_cloud", []).append({"url": url, "post": post})
        with lock:
            allst = _read(_fake_store(), {})
            f(allst)
            with open(_fake_store(), "w", encoding="utf-8") as fh:
                json.dump(allst, fh)
        return True
    sig = (t["region"], t["api_key"], t["api_secret"])
    if _cloud["obj"] is None or _cloud["sig"] != sig:
        import tinytuya
        c = tinytuya.Cloud(apiRegion=t["region"], apiKey=t["api_key"], apiSecret=t["api_secret"])
        if getattr(c, "error", None):
            raise CloudError(f"Tuya cloud: {c.error}")
        _cloud.update(obj=c, sig=sig)
    res = _cloud["obj"].cloudrequest(url, action=action, post=post)
    if not isinstance(res, dict):
        raise CloudError("Tuya cloud: no answer")
    if not res.get("success"):
        if _retry and str(res.get("code")) in ("1010", "1011", "1012"):  # token expired / invalid
            _cloud["obj"] = None
            return cloud_call(url, post, action, _retry=False)
        raise CloudError(f"Tuya cloud: {res.get('msg') or res.get('Error') or res} (code {res.get('code')})")
    return res.get("result")


# ------------------------------------------------------------------ syncing with the iHost
def ihost_devices():
    res = ihost("GET", "/devices")
    if res.get("error") != 0:
        raise IHostError(f"iHost error {res.get('error')}: {res.get('message')}")
    return res["data"].get("device_list", [])


def add_to_ihost(dev_id):
    dev = CFG["devices"][dev_id]
    special = special_kind(dev)
    if special == "lock":
        raise IHostError("Door locks can't be controlled locally - they are shown in the iHost Home app (cloud).")
    if special == "ir_hub":
        raise IHostError("The IR transmitter itself has nothing to switch - add the remotes that use it (e.g. the AC).")
    if special == "ir_other":
        raise IHostError("Only IR air conditioners are supported so far.")
    if special == "ac":
        hub = hub_for(dev)
        if not hub:
            raise IHostError("Pick which IR transmitter this AC uses (Edit), then try again.")
        st = link_for(hub).status() if (hub.get("ip") or FAKE) else {"Error": "IR transmitter IP unknown - run Scan"}
        ok = reachable(st)
    else:
        if not dev.get("ip") and not FAKE:
            raise IHostError("This device has no IP yet - run Scan first (or type its IP).")
        st = link_for(dev).status()
        ok = tuya_ok(st)
        if ok:
            dev["last_dps"] = st.get("dps") or {}
    res = ihost_event("DiscoveryRequest", {"endpoints": [endpoint_for(dev)]})
    eps = (res.get("payload") or {}).get("endpoints") or []
    sn = next((e.get("serial_number") for e in eps if e.get("third_serial_number") == "tuya_" + dev_id), None)
    if not sn:  # some firmwares answer without the list: look it up
        sn = next((d["serial_number"] for d in ihost_devices() if d.get("third_serial_number") == "tuya_" + dev_id), None)
    if not sn:
        raise IHostError("The iHost accepted the device but didn't return its serial number")
    dev.update(ihost_sn=sn, enabled=True, online=ok)
    save()
    if not ok:
        report_online(dev, False)
    else:  # the iHost sometimes stores the discovery state a moment late; report it explicitly
        report_state(dev, endpoint_for(dev)["state"])
    log("added to iHost:", dev.get("name"), sn)
    return sn


def remove_from_ihost(dev_id):
    dev = CFG["devices"][dev_id]
    sn = dev.get("ihost_sn")
    if sn:
        try:
            ihost("DELETE", "/devices/" + urllib.parse.quote(sn))
        except IHostError as e:
            log("remove failed:", e)
    dev.update(ihost_sn=None, enabled=False)
    save()


def report_state(dev, state):
    if not dev.get("ihost_sn"):
        return
    try:
        ihost_event("DeviceStatesChangeReport", {"state": state},
                    {"serial_number": dev["ihost_sn"], "third_serial_number": "tuya_" + dev["id"]})
    except IHostError as e:
        log("state report failed:", dev.get("name"), e)


def report_online(dev, online):
    if not dev.get("ihost_sn"):
        return
    try:
        ihost_event("DeviceOnlineChangeReport", {"online": bool(online)},
                    {"serial_number": dev["ihost_sn"], "third_serial_number": "tuya_" + dev["id"]})
    except IHostError as e:
        log("online report failed:", dev.get("name"), e)


def resync_all():
    """At start-up: drop devices the user deleted in the iHost, and re-announce state/online for the rest
    (the iHost marks third-party devices offline while their service was unreachable)."""
    if not CFG["ihost"]["token"]:
        return
    try:
        present = {d.get("third_serial_number"): d["serial_number"] for d in ihost_devices()}
    except IHostError as e:
        log("resync skipped:", e)
        return
    for dev in list(CFG["devices"].values()):
        if not dev.get("ihost_sn"):
            continue
        sn = present.get("tuya_" + dev["id"])
        if not sn:
            log("removed in the iHost, unlinking:", dev.get("name"))
            dev.update(ihost_sn=None, enabled=False)
            continue
        dev["ihost_sn"] = sn
        dev["_reported"] = None  # force a fresh report on the next poll
        dev["_online_reported"] = None
    save()


# ------------------------------------------------------------------ control: iHost -> Tuya
def ac_code_key(s):
    if s["power"] == "off":
        return "off"
    return s["mode"] if s["mode"] in ("fan", "dry") else f"{s['mode']}_{s['temp']}"


def send_ac(dev, s):
    """Learned code first (100% local); otherwise Tuya's cloud AC command (needs the cloud trial active)."""
    hub = hub_for(dev)
    if not hub:
        raise IHostError("No IR transmitter linked to this AC")
    code = (dev.get("codes") or {}).get(ac_code_key(s))
    if code:
        ir_link(hub).send(code)
        return "local"
    if cloud_ready():
        body = {"power": 1 if s["power"] == "on" else 0, "mode": AC_MODES.index(s["mode"]), "temp": int(s["temp"]),
                "wind": AC_FANS.index(s["fan"])}
        cloud_call(f"/v2.0/infrareds/{hub['id']}/air-conditioners/{dev['id']}/scenes/command", post=body, action="POST")
        return "cloud"
    raise IHostError(f"No learned IR code for '{ac_code_key(s)}' and no Tuya cloud keys to fall back on")


def apply_ac(dev, state):
    cur = ac_state(dev)
    new = dict(cur)
    ps = (state.get("power") or {}).get("powerState")
    if ps:
        new["power"] = ("off" if cur["power"] == "on" else "on") if ps == "toggle" else ps
    for name, v in (state.get("mode") or {}).items():
        val = (v or {}).get("modeValue")
        if name == "airConditionerMode" and val in AC_MODES:
            new["mode"] = val
        elif name == "fanLevel" and val in AC_FANS:
            new["fan"] = val
    for v in (state.get("thermostat-target-setpoint") or {}).values():
        try:
            new["temp"] = max(16, min(30, int(round(float((v or {}).get("targetSetpoint"))))))
        except (TypeError, ValueError):
            pass
    if new == cur and not ps:
        return True, ac_ihost_state(new)
    try:
        if new["power"] == "on" or cur["power"] == "on":  # settings changed while off are just remembered
            dev["ac_via"] = send_ac(dev, new)
        dev["last_error"] = None
    except Exception as e:
        dev["last_error"] = str(e)
        log("AC command failed:", dev.get("name"), e)
        return False, "ENDPOINT_UNREACHABLE"
    dev["ac"] = new
    save()
    return True, ac_ihost_state(new)


def apply_directive(dev, state):
    special = special_kind(dev)
    if special == "ac":
        return apply_ac(dev, state)
    if special:
        return False, "INVALID_DIRECTIVE"
    d = describe(dev)
    cur = dev.get("last_dps") or {}
    values = {}

    def want(v, current):
        return (not current) if v == "toggle" else (v == "on")

    if "toggle" in state and d["channels"]:
        by_ch = {c["ch"]: c["dps"] for c in d["channels"]}
        for ch, v in (state["toggle"] or {}).items():
            if str(ch) in by_ch:
                dps = by_ch[str(ch)]
                values[dps] = want(v.get("toggleState"), cur.get(dps))
    if "power" in state:
        ps = (state["power"] or {}).get("powerState")
        targets = [c["dps"] for c in d["channels"]] if d["channels"] else [d["single"]]
        anyon = any(cur.get(t) for t in targets)
        for t in targets:
            values[t] = (not anyon) if ps == "toggle" else (ps == "on")
    if "brightness" in state and d["bright"]:
        values[d["bright"]["dps"]] = from_pct((state["brightness"] or {}).get("brightness", 100), d["bright"])
        if d["single"]:
            values.setdefault(d["single"], True)
    if not values:
        return False, "INVALID_DIRECTIVE"
    res = link_for(dev).set_dps(values)
    if not tuya_ok(res):
        return False, "ENDPOINT_UNREACHABLE"
    merged = dict(cur)
    merged.update(values)
    merged.update((res or {}).get("dps") or {})
    dev["last_dps"] = merged
    new_state = ihost_state_from_dps(dev, merged)
    dev["_reported"] = json.dumps(new_state, sort_keys=True)  # the iHost already knows - don't echo it back
    return True, new_state


# ------------------------------------------------------------------ polling: Tuya -> iHost
def set_online(dev, online, why=None):
    dev["online"] = online
    if dev.get("ihost_sn") and online != dev.get("_online_reported"):
        dev["_online_reported"] = online
        report_online(dev, online)
        if not online:
            log("offline:", dev.get("name"), why or "")


def poll_hub(hub):
    """IR transmitter: online = answers on the network. Its ACs follow it."""
    if not hub.get("ip") and not FAKE:
        return
    res = link_for(hub).status()
    online = reachable(res)
    set_online(hub, online, (res or {}).get("Error") if isinstance(res, dict) else None)
    if online and not FAKE and not hub.get("ir_control_type") and hub["id"] not in DETECTING:
        DETECTING.add(hub["id"])  # first IR send would otherwise spend ~3 s detecting the protocol

        def detect():
            try:
                with link_for(hub).lock:
                    ir_link(hub)._device()
            except Exception as e:
                log("IR transmitter type detection failed:", e)
            finally:
                DETECTING.discard(hub["id"])
        threading.Thread(target=detect, daemon=True).start()
    for d in CFG["devices"].values():
        if special_kind(d) == "ac" and d.get("ihost_sn") and hub_for(d) is hub:
            set_online(d, online, "IR transmitter unreachable")


def poll_one(dev):
    special = special_kind(dev)
    if special == "ir_hub":
        return poll_hub(dev)
    if special:
        return
    if not dev.get("ihost_sn") or (not dev.get("ip") and not FAKE):
        return
    res = link_for(dev).status()
    online = tuya_ok(res)
    set_online(dev, online, (res or {}).get("Error"))
    if not online:
        return
    dps = dict(dev.get("last_dps") or {})
    dps.update(res.get("dps") or {})
    dev["last_dps"] = dps
    state = ihost_state_from_dps(dev, dps)
    sig = json.dumps(state, sort_keys=True)
    if sig != dev.get("_reported"):
        dev["_reported"] = sig
        report_state(dev, state)


def poll_targets():
    out = []
    for d in CFG["devices"].values():
        sk = special_kind(d)
        if sk == "ir_hub":
            used = d.get("ihost_sn") or any(special_kind(x) == "ac" and x.get("ihost_sn") and hub_for(x) is d
                                            for x in CFG["devices"].values())
            if used:
                out.append(d)
        elif not sk and d.get("ihost_sn"):
            out.append(d)
    return out


def poller():
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    while True:
        start = time.time()
        devs = poll_targets()
        if devs and CFG["ihost"]["token"]:
            list(pool.map(lambda d: _safe(poll_one, d), devs))
        time.sleep(max(0.5, POLL_SECONDS - (time.time() - start)))


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # never let one device stop the loop
        log("error:", fn.__name__, e)


# ------------------------------------------------------------------ import + scan
def merge_devices(items, source):
    added = 0
    for it in items:
        dev_id = str(it.get("id") or it.get("gwId") or "").strip()
        if not dev_id:
            continue
        cur = CFG["devices"].get(dev_id, {"id": dev_id, "enabled": False, "ihost_sn": None})
        for k in ("name", "key", "category", "product_name", "model", "mapping", "ip", "version", "mac", "dps_channels",
                  "gateway_id", "parent", "node_id"):
            v = it.get(k) if k != "key" else (it.get("key") or it.get("local_key"))
            if v not in (None, "", {}):
                cur[k] = v
        cur["source"] = source
        if dev_id not in CFG["devices"]:
            added += 1
        CFG["devices"][dev_id] = cur
    save()
    return added


def cloud_import(region, api_key, api_secret, any_device_id):
    if FAKE:
        raise IHostError("cloud import isn't available in test mode")
    import tinytuya
    c = tinytuya.Cloud(apiRegion=region, apiKey=api_key, apiSecret=api_secret, apiDeviceID=any_device_id or None)
    if getattr(c, "error", None):
        raise IHostError(f"Tuya cloud: {c.error}")
    devs = c.getdevices(verbose=False, include_map=True)
    if isinstance(devs, dict) and devs.get("Error"):
        raise IHostError(f"Tuya cloud: {devs.get('Error')} {devs.get('Payload') or ''}".strip())
    return merge_devices(devs or [], "cloud")


SCAN = {"state": "idle", "found": 0, "message": ""}


def scan_worker():
    SCAN.update(state="scanning", found=0, message="Listening for Tuya devices (about 20 seconds)...")
    try:
        if FAKE:
            time.sleep(1)
            found = {d["id"]: {"ip": f"10.0.0.{i + 10}", "version": "3.4"} for i, d in enumerate(CFG["devices"].values())}
        else:
            import tinytuya
            found = tinytuya.deviceScan(verbose=False, maxretry=None, color=False, poll=False, byID=True)
        n = 0
        for dev_id, info in (found or {}).items():
            dev = CFG["devices"].get(dev_id)
            if dev is None:
                CFG["devices"][dev_id] = dev = {"id": dev_id, "name": f"Tuya {dev_id[-6:]}", "enabled": False,
                                                "ihost_sn": None, "source": "scan"}
            dev["ip"] = info.get("ip") or dev.get("ip")
            if info.get("version"):
                dev["version"] = str(info["version"])
            n += 1
        save()
        missing_key = sum(1 for d in CFG["devices"].values() if not d.get("key"))
        SCAN.update(state="done", found=n, message=f"Found {n} Tuya device(s) on the network."
                    + (f" {missing_key} still need a local key - import them from the Tuya cloud." if missing_key else ""))
    except Exception as e:
        SCAN.update(state="error", message=f"Scan failed: {e}")


# ------------------------------------------------------------------ web
LEARN = {}  # dev_id -> {"state": "waiting"|"done"|"error", "key":..., "message":...}

SPECIAL_NOTES = {
    "lock": "Door lock: no local control. Status, history and unlock are in the iHost Home app (Tuya cloud).",
    "ir_hub": "IR transmitter: it sends the codes for your IR remotes (e.g. the AC). It doesn't need to be in the iHost.",
    "ir_other": "IR remote: only air conditioners are supported so far.",
}


def learn_worker(dev_id, key):
    dev = CFG["devices"].get(dev_id)
    hub = hub_for(dev) if dev else None
    if not hub:
        LEARN[dev_id] = {"state": "error", "key": key, "message": "No IR transmitter linked to this AC"}
        return
    LEARN[dev_id] = {"state": "waiting", "key": key,
                     "message": "Point the AC's remote at the IR transmitter and press the button now (15 s)..."}
    try:
        code = ir_link(hub).learn(15)
    except Exception as e:
        LEARN[dev_id] = {"state": "error", "key": key, "message": f"Learning failed: {e}"}
        return
    if not code:
        LEARN[dev_id] = {"state": "error", "key": key, "message": "Nothing received - try again, closer to the transmitter."}
        return
    dev.setdefault("codes", {})[key] = code
    save()
    LEARN[dev_id] = {"state": "done", "key": key, "message": f"Learned '{key}'."}
    log("learned IR code", key, "for", dev.get("name"))


def public_device(d):
    desc = describe(d)
    special = desc.get("special")
    out = {"id": d["id"], "name": d.get("name"), "ip": d.get("ip"), "version": d.get("version"),
           "has_key": bool(d.get("key")), "category": d.get("category"), "product": d.get("product_name"),
           "kind": special or desc.get("display"), "special": special, "note": SPECIAL_NOTES.get(special),
           "channels": len(desc.get("channels") or []) or 1, "dimmable": bool(desc.get("bright")),
           "in_ihost": bool(d.get("ihost_sn")), "online": d.get("online"),
           "addable": special not in ("lock", "ir_hub", "ir_other"),
           "state": ihost_state_from_dps(d, d.get("last_dps") or {}) if d.get("last_dps") and not special else None}
    if special == "ac":
        hub = hub_for(d)
        out.update(ac=ac_state(d), codes=sorted((d.get("codes") or {}).keys()), hub=hub and {"id": hub["id"], "name": hub.get("name")},
                   via=d.get("ac_via"), last_error=d.get("last_error"), learn=LEARN.get(d["id"]), cloud=cloud_ready())
    if special == "ir_hub":
        out["online"] = d.get("online")
    return out


def check_ui_auth(handler):
    pw = os.environ.get("UI_PASSWORD")
    if not pw:
        return True
    expected = "Basic " + __import__("base64").b64encode(f"admin:{pw}".encode()).decode()
    return hmac.compare_digest(handler.headers.get("Authorization") or "", expected)


class Handler(BaseHTTPRequestHandler):
    server_version = f"TuyaBridge/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if 0 < n <= 2_000_000 else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        path = urllib.parse.urlparse(self.path).path
        body = self.read_body() if method in ("POST", "DELETE") else {}
        try:
            # ---- the iHost's control channel (secret path, no UI auth)
            if path.startswith("/ihost/") and method == "POST":
                parts = path.strip("/").split("/")
                if len(parts) != 3 or not hmac.compare_digest(parts[1], CFG["hook_secret"]) or parts[2] != "directive":
                    return self.send_json({"error": "not found"}, 404)
                return self.directive(body)
            if path == "/healthz":
                return self.send_json({"ok": True, "version": VERSION})
            if not check_ui_auth(self):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Tuya Bridge"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not path.startswith("/api/"):
                return self.static(path)
            origin = self.headers.get("Origin")
            if method != "GET" and origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
                return self.send_json({"error": "cross-origin request blocked"}, 403)
            return self.api(method, path, body)
        except IHostError as e:
            self.send_json({"error": str(e)}, 502)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # never drop the connection without an answer
            log("request error:", path, repr(e))
            try:
                self.send_json({"error": f"internal error: {e}"}, 500)
            except OSError:
                pass

    def directive(self, body):
        d = body.get("directive") or {}
        header = d.get("header") or {}
        mid = header.get("message_id", "")
        ep = d.get("endpoint") or {}
        tsn = ep.get("third_serial_number") or ""
        dev = CFG["devices"].get(tsn[5:] if tsn.startswith("tuya_") else tsn)

        def reply(name, payload):
            return self.send_json({"event": {"header": {"name": name, "message_id": mid, "version": "1"}, "payload": payload}})

        if header.get("name") != "UpdateDeviceStates" or not dev:
            return reply("ErrorResponse", {"type": "INVALID_DIRECTIVE" if dev else "ENDPOINT_UNREACHABLE"})
        ok, result = apply_directive(dev, (d.get("payload") or {}).get("state") or {})
        log("command", dev.get("name"), json.dumps((d.get("payload") or {}).get("state")), "->", "ok" if ok else result)
        if not ok:
            return reply("ErrorResponse", {"type": result})
        return reply("UpdateDeviceStatesResponse", {})

    def api(self, method, path, b):
        parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/")]
        if path == "/api/status" and method == "GET":
            info, err = None, None
            if CFG["ihost"]["token"]:
                try:
                    info = ihost("GET", "/bridge", timeout=4).get("data")
                except IHostError as e:
                    err = str(e)
            t = CFG["tuya"]
            return self.send_json({
                "version": VERSION, "fake": FAKE, "ihost": {"host": CFG["ihost"]["host"], "paired": bool(CFG["ihost"]["token"]),
                                                            "bridge": info, "error": err},
                "service_address": service_base(), "pair": PAIR, "scan": SCAN,
                "tuya": {"region": t["region"], "api_key": t["api_key"], "has_secret": bool(t["api_secret"]), "device_id": t["device_id"]},
                "devices": sorted((public_device(d) for d in CFG["devices"].values()), key=lambda x: (x["name"] or "").lower())})
        if path == "/api/ihost" and method == "POST":
            host = str(b.get("host", "")).strip()
            if host:
                CFG["ihost"]["host"] = host
                save()
            if PAIR["state"] != "waiting":
                threading.Thread(target=pair_worker, daemon=True).start()
            return self.send_json({"ok": True})
        if path == "/api/import/json" and method == "POST":
            data = b.get("devices")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    return self.send_json({"error": "That isn't valid JSON. Paste the whole devices.json file."}, 400)
            if isinstance(data, dict):
                data = data.get("devices") or data.get("result") or [data]
            if not isinstance(data, list):
                return self.send_json({"error": "Expected a list of devices (devices.json)."}, 400)
            return self.send_json({"added": merge_devices(data, "devices.json"), "total": len(CFG["devices"])})
        if path == "/api/import/cloud" and method == "POST":
            t = CFG["tuya"]
            t["region"] = str(b.get("region") or t["region"]).strip()
            t["api_key"] = str(b.get("api_key") or t["api_key"]).strip()
            if b.get("api_secret"):
                t["api_secret"] = str(b["api_secret"]).strip()
            t["device_id"] = str(b.get("device_id") or t["device_id"]).strip()
            save()
            if not (t["api_key"] and t["api_secret"]):
                return self.send_json({"error": "Enter the Access ID and Access Secret from your Tuya cloud project."}, 400)
            added = cloud_import(t["region"], t["api_key"], t["api_secret"], t["device_id"])
            return self.send_json({"added": added, "total": len(CFG["devices"])})
        if path == "/api/scan" and method == "POST":
            if SCAN["state"] != "scanning":
                threading.Thread(target=scan_worker, daemon=True).start()
            return self.send_json({"ok": True})
        if len(parts) >= 3 and parts[1] == "devices":
            dev = CFG["devices"].get(parts[2])
            if not dev:
                return self.send_json({"error": "unknown device"}, 404)
            action = parts[3] if len(parts) > 3 else ""
            if action == "add" and method == "POST":
                if not CFG["ihost"]["token"]:
                    return self.send_json({"error": "Connect the iHost first."}, 400)
                if not dev.get("key") and special_kind(dev) != "ac":
                    return self.send_json({"error": "This device has no local key - import it from the Tuya cloud or devices.json."}, 400)
                return self.send_json({"serial_number": add_to_ihost(dev["id"])})
            if action == "remove" and method == "POST":
                remove_from_ihost(dev["id"])
                return self.send_json({"ok": True})
            if action == "edit" and method == "POST":
                for k in ("ip", "version", "name"):
                    if k in b and str(b[k]).strip():
                        dev[k] = str(b[k]).strip()
                if "dps_channels" in b:
                    dev["dps_channels"] = [str(x).strip() for x in str(b["dps_channels"]).split(",") if str(x).strip()]
                if b.get("ir_hub") and b["ir_hub"] in CFG["devices"]:
                    dev["ir_hub"] = b["ir_hub"]
                save()
                return self.send_json(public_device(dev))
            if action == "learn" and method == "POST":
                key = str(b.get("key") or "").strip()
                if special_kind(dev) != "ac" or not (key == "off" or key in ("fan", "dry")
                                                     or (key.split("_")[0] in AC_MODES and key.split("_")[-1].isdigit())):
                    return self.send_json({"error": "Pick a button to learn"}, 400)
                if (LEARN.get(dev["id"]) or {}).get("state") != "waiting":
                    threading.Thread(target=learn_worker, args=(dev["id"], key), daemon=True).start()
                return self.send_json({"ok": True})
            if action == "send-code" and method == "POST":
                code = (dev.get("codes") or {}).get(str(b.get("key")))
                hub = hub_for(dev)
                if not code or not hub:
                    return self.send_json({"error": "No such learned code"}, 404)
                ir_link(hub).send(code)
                return self.send_json({"ok": True})
            if action == "delete-code" and method == "POST":
                (dev.get("codes") or {}).pop(str(b.get("key")), None)
                save()
                return self.send_json({"ok": True})
            if action == "test" and method == "POST":
                special = special_kind(dev)
                target = hub_for(dev) if special == "ac" else dev
                if special == "lock":
                    return self.send_json({"ok": False, "error": SPECIAL_NOTES["lock"]})
                if not target:
                    return self.send_json({"ok": False, "error": "No IR transmitter linked"})
                res = link_for(target).status()
                if special in ("ir_hub", "ac", "ir_other"):
                    ok = reachable(res)
                    target["online"] = ok
                    return self.send_json({"ok": ok, "dps": "IR transmitter answered" if ok else None,
                                           "error": None if ok else (res or {}).get("Error")})
                if tuya_ok(res):
                    dev["last_dps"] = res.get("dps") or {}
                    dev["online"] = True
                    save()
                    return self.send_json({"ok": True, "dps": res.get("dps")})
                return self.send_json({"ok": False, "error": (res or {}).get("Error") or "no answer"}, 200)
            if action == "set" and method == "POST":
                ok, result = apply_directive(dev, b.get("state") or {})
                if ok:
                    report_state(dev, result)
                return self.send_json({"ok": ok, "state": result if ok else None, "error": None if ok else result})
            if action == "" and method == "DELETE":
                if dev.get("ihost_sn"):
                    remove_from_ihost(dev["id"])
                CFG["devices"].pop(dev["id"], None)
                LINKS.pop(dev["id"], None)
                save()
                return self.send_json({"ok": True})
        return self.send_json({"error": "not found"}, 404)

    def static(self, path):
        full = os.path.normpath(os.path.join(WEB, (path.lstrip("/") or "index.html")))
        if not full.startswith(WEB) or not os.path.isfile(full):
            full = os.path.join(WEB, "index.html")
        with open(full, "rb") as f:
            data = f.read()
        ctype = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(
            os.path.splitext(full)[1], "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)


def main():
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def handle_error(self, request, client_address):
            if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
                return
            super().handle_error(request, client_address)

    srv = Server(("0.0.0.0", PORT), Handler)
    log(f"Tuya Bridge {VERSION} on http://0.0.0.0:{PORT}  (data: {DATA}{', TEST MODE' if FAKE else ''})")
    log("iHost:", CFG["ihost"]["host"], "| paired" if CFG["ihost"]["token"] else "| not paired yet")
    threading.Thread(target=resync_all, daemon=True).start()
    threading.Thread(target=poller, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
