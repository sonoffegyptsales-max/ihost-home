"""E2E: IR air conditioner through the bridge, against a REAL iHost, with a simulated IR transmitter + Tuya cloud.
usage: python e2e_ac.py <ihost_url> <token>"""
import json, os, shutil, subprocess, sys, time, urllib.request, urllib.error

IHOST, TOKEN = sys.argv[1].strip().rstrip("/"), sys.argv[2].strip()
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.environ.get("TMPDIR") or HERE, "tb-e2e-ac")
PORT = 8323
B = f"http://127.0.0.1:{PORT}"
fails = 0


def check(label, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + label + (f"  [{extra}]" if extra and not ok else ""), flush=True)
    fails += 0 if ok else 1


def http(method, url, body=None, token=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def ih(method, path, body=None):
    return http(method, IHOST + "/open-api/v2/rest" + path, body, TOKEN)


def ihost_dev(sn):
    return next((d for d in ih("GET", "/devices")["data"]["device_list"] if d["serial_number"] == sn), None)


def wait(fn, t=15):
    end = time.time() + t
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(0.4)
    return None


def store():
    return json.load(open(os.path.join(DATA, "fake_tuya.json")))


def store_set(dev, **kv):
    s = store()
    s.setdefault(dev, {}).update(kv)
    json.dump(s, open(os.path.join(DATA, "fake_tuya.json"), "w"))


HUB, AC, LOCK = "tb_test_irhub", "tb_test_ac", "tb_test_lock"
shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA)
json.dump({"ihost": {"host": IHOST, "token": TOKEN}, "tuya": {"region": "eu", "api_key": "fake", "api_secret": "fake"}},
          open(os.path.join(DATA, "config.json"), "w"))
json.dump({HUB: {}}, open(os.path.join(DATA, "fake_tuya.json"), "w"))
DEVICES = [
    {"name": "ZZ Test IR", "id": HUB, "key": "0123456789abcdef", "category": "wnykq", "product_name": "Smart IR"},
    {"name": "ZZ Test AC", "id": AC, "key": "0123456789abcdef", "category": "infrared_ac", "product_name": "AC"},
    {"name": "ZZ Test Lock", "id": LOCK, "key": "0123456789abcdef", "category": "videolock", "product_name": "Smart Lock"},
]
env = dict(os.environ, BRIDGE_FAKE_TUYA="1", DATA_DIR=DATA, PORT=str(PORT), POLL_SECONDS="1.5")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "bridge.py")], env=env,
                        stdout=open(os.path.join(DATA, "bridge.log"), "w"), stderr=subprocess.STDOUT)
sn = None
try:
    check("bridge starts", wait(lambda: http("GET", B + "/healthz").get("ok"), 20))
    http("POST", B + "/api/import/json", {"devices": json.dumps(DEVICES)})
    wait(lambda: http("POST", B + "/api/scan", {}) and http("GET", B + "/api/status")["scan"]["state"] == "done")
    st = {d["id"]: d for d in http("GET", B + "/api/status")["devices"]}
    check("kinds: IR transmitter / AC / lock", (st[HUB]["special"], st[AC]["special"], st[LOCK]["special"]) == ("ir_hub", "ac", "lock"))
    check("AC auto-linked to the only IR transmitter", (st[AC].get("hub") or {}).get("id") == HUB, st[AC].get("hub"))
    r = http("POST", f"{B}/api/devices/{LOCK}/add", {})
    check("lock is not added to the iHost (explained)", "error" in r and "lock" in r["error"].lower(), r)
    r = http("POST", f"{B}/api/devices/{HUB}/add", {})
    check("IR transmitter itself is not added", "error" in r, r)
    r = http("POST", f"{B}/api/devices/{HUB}/test", {})
    check("IR transmitter test = online (empty status is OK)", r.get("ok") is True, r)

    r = http("POST", f"{B}/api/devices/{AC}/add", {})
    sn = r.get("serial_number")
    check("AC added to the iHost", bool(sn), r)
    d = wait(lambda: (lambda x: x if x and x.get("state") and "thermostat-target-setpoint" in x["state"] else None)(ihost_dev(sn)), 10) or ihost_dev(sn)
    caps = sorted((c["capability"], c.get("name")) for c in d["capabilities"]) if d else []
    check("iHost shows it as airConditioner", d and d["display_category"] == "airConditioner", d and d["display_category"])
    check("iHost accepted mode/fan/temperature capabilities",
          ("mode", "airConditionerMode") in caps and ("mode", "fanLevel") in caps and ("thermostat-target-setpoint", "manual-mode") in caps, caps)
    check("initial state off / cool 24", d and d["state"]["power"]["powerState"] == "off"
          and d["state"]["thermostat-target-setpoint"]["manual-mode"]["targetSetpoint"] == 24, d and d["state"])

    # power on, no learned code -> cloud fallback
    r = ih("PUT", f"/devices/{sn}", {"state": {"power": {"powerState": "on"}}})
    calls = store().get("_cloud", [])
    check("power ON via iHost -> cloud AC command", r.get("error") == 0 and calls and calls[-1]["post"] == {"power": 1, "mode": 0, "temp": 24, "wind": 0}
          and calls[-1]["url"] == f"/v2.0/infrareds/{HUB}/air-conditioners/{AC}/scenes/command", (r, calls[-1:]))

    # learn cool_22 then set 22 -> local
    http("POST", f"{B}/api/devices/{AC}/learn", {"key": "cool_22"})
    time.sleep(0.8)
    store_set(HUB, _learn_next="FAKECODE_COOL22")
    learned = wait(lambda: "cool_22" in http("GET", B + "/api/status") and False or
                   "cool_22" in next(x for x in http("GET", B + "/api/status")["devices"] if x["id"] == AC)["codes"], 10)
    check("learning 'cool_22' from the remote", bool(learned))
    n_cloud = len(store().get("_cloud", []))
    r = ih("PUT", f"/devices/{sn}", {"state": {"thermostat-target-setpoint": {"manual-mode": {"targetSetpoint": 22}}}})
    s = store()
    check("set 22° -> learned code sent LOCALLY (no cloud)", r.get("error") == 0 and s[HUB].get("_sent", [])[-1:] == ["FAKECODE_COOL22"]
          and len(s.get("_cloud", [])) == n_cloud, (r, s[HUB].get("_sent")))
    r = ih("PUT", f"/devices/{sn}", {"state": {"mode": {"airConditionerMode": {"modeValue": "heat"}}}})
    calls = store().get("_cloud", [])
    check("mode heat (not learned) -> cloud with heat/22", r.get("error") == 0 and calls[-1]["post"] == {"power": 1, "mode": 1, "temp": 22, "wind": 0}, calls[-1:])
    r = ih("PUT", f"/devices/{sn}", {"state": {"mode": {"fanLevel": {"modeValue": "high"}}}})
    calls = store().get("_cloud", [])
    check("fan high -> cloud wind 3", r.get("error") == 0 and calls[-1]["post"]["wind"] == 3, calls[-1:])
    d = ihost_dev(sn)
    check("iHost state now heat / 22 / high / on", d["state"]["mode"]["airConditionerMode"]["modeValue"] == "heat"
          and d["state"]["thermostat-target-setpoint"]["manual-mode"]["targetSetpoint"] == 22
          and d["state"]["mode"]["fanLevel"]["modeValue"] == "high" and d["state"]["power"]["powerState"] == "on", d["state"])
    r = ih("PUT", f"/devices/{sn}", {"state": {"power": {"powerState": "off"}}})
    calls = store().get("_cloud", [])
    check("power OFF -> cloud power 0", r.get("error") == 0 and calls[-1]["post"]["power"] == 0, calls[-1:])
    n_cloud = len(calls)
    r = ih("PUT", f"/devices/{sn}", {"state": {"thermostat-target-setpoint": {"manual-mode": {"targetSetpoint": 26}}}})
    check("changing temp while OFF sends nothing (just remembered)", r.get("error") == 0 and len(store().get("_cloud", [])) == n_cloud)

    # transmitter offline -> AC offline
    store_set(HUB, _offline=True)
    check("IR transmitter unplugged -> AC offline in iHost", wait(lambda: ihost_dev(sn)["online"] is False, 10))
    store_set(HUB, _offline=False)
    check("IR transmitter back -> AC online", wait(lambda: ihost_dev(sn)["online"] is True, 10))
finally:
    if sn:
        http("POST", f"{B}/api/devices/{AC}/remove", {})
    time.sleep(1)
    left = [d["name"] for d in ih("GET", "/devices")["data"]["device_list"] if (d.get("third_serial_number") or "").startswith("tuya_tb_test")]
    check("cleanup: test devices removed from the iHost", not left, left)
    proc.terminate()
    print("FAILURES:", fails)
    print(open(os.path.join(DATA, "bridge.log")).read()[-1800:])
