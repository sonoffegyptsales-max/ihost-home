"""End-to-end test of the Tuya bridge against a REAL iHost, with simulated Tuya devices (BRIDGE_FAKE_TUYA=1).

Adds 3 temporary test devices to the iHost, drives them both ways, then removes them.
usage: python e2e_real_ihost.py <ihost_url> <token>
"""
import json, os, shutil, subprocess, sys, time, urllib.request, urllib.error

IHOST, TOKEN = sys.argv[1].strip().rstrip("/"), sys.argv[2].strip()
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.environ.get("TMPDIR") or HERE, "tb-e2e-data")
PORT = 8321
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
        with urllib.request.urlopen(req, timeout=15) as r:
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
        time.sleep(0.5)
    return None


def fake_set(dev_id, **dps):
    p = os.path.join(DATA, "fake_tuya.json")
    st = json.load(open(p))
    st[dev_id].update(dps)
    json.dump(st, open(p, "w"))


shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA)
json.dump({"ihost": {"host": IHOST, "token": TOKEN}}, open(os.path.join(DATA, "config.json"), "w"))
# simulated hardware: 2-gang switch, dimmable bulb, plug
json.dump({"tb_test_2gang": {"1": False, "2": False},
           "tb_test_bulb": {"20": False, "22": 500},
           "tb_test_plug": {"1": True}}, open(os.path.join(DATA, "fake_tuya.json"), "w"))
DEVICES = [
    {"name": "ZZ Test Tuya 2-gang", "id": "tb_test_2gang", "key": "0123456789abcdef", "category": "kg", "product_name": "Wi-Fi 2 Gang Switch",
     "mapping": {"1": {"code": "switch_1", "type": "Boolean"}, "2": {"code": "switch_2", "type": "Boolean"}}},
    {"name": "ZZ Test Tuya bulb", "id": "tb_test_bulb", "key": "0123456789abcdef", "category": "dj", "product_name": "Smart Bulb",
     "mapping": {"20": {"code": "switch_led", "type": "Boolean"}, "22": {"code": "bright_value_v2", "type": "Integer", "values": {"min": 10, "max": 1000}}}},
    {"name": "ZZ Test Tuya plug", "id": "tb_test_plug", "key": "0123456789abcdef", "category": "cz", "product_name": "Smart Plug",
     "mapping": {"1": {"code": "switch_1", "type": "Boolean"}}},
]

env = dict(os.environ, BRIDGE_FAKE_TUYA="1", DATA_DIR=DATA, PORT=str(PORT), POLL_SECONDS="1.5")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "bridge.py")], env=env,
                        stdout=open(os.path.join(DATA, "bridge.log"), "w"), stderr=subprocess.STDOUT)
sns = {}
try:
    check("bridge starts", wait(lambda: http("GET", B + "/healthz").get("ok"), 20))
    st = http("GET", B + "/api/status")
    check("talks to the real iHost", bool(st["ihost"]["bridge"]), st["ihost"].get("error"))
    print("   service address the iHost will call:", st["service_address"])
    r = http("POST", B + "/api/import/json", {"devices": json.dumps(DEVICES)})
    check("devices.json import", r.get("added") == 3, r)
    r = http("POST", B + "/api/scan", {})
    check("scan finds IPs/versions", wait(lambda: http("GET", B + "/api/status")["scan"]["state"] == "done"))
    for d in DEVICES:
        r = http("POST", f"{B}/api/devices/{d['id']}/add", {})
        sns[d["id"]] = r.get("serial_number")
        check(f"add '{d['name']}' to iHost", bool(r.get("serial_number")), r)

    two, bulb, plug = (ihost_dev(sns[k]) for k in ("tb_test_2gang", "tb_test_bulb", "tb_test_plug"))
    check("2-gang shows in iHost as switch with 2 channels",
          two and two["display_category"] == "switch" and set(two["state"].get("toggle", {})) == {"1", "2"}, two and two.get("state"))
    check("bulb shows as dimmable light at 49%",
          bulb and bulb["display_category"] == "light" and bulb["state"].get("brightness", {}).get("brightness") == 49, bulb and bulb.get("state"))
    check("plug shows as plug, ON, online",
          plug and plug["display_category"] == "plug" and plug["state"]["power"]["powerState"] == "on" and plug["online"], plug)

    # ---- iHost -> Tuya (what the app / Vercel / scenes do)
    t0 = time.time()
    r = ih("PUT", "/devices/" + sns["tb_test_2gang"], {"state": {"toggle": {"2": {"toggleState": "on"}}}})
    ms = int((time.time() - t0) * 1000)
    hw = json.load(open(os.path.join(DATA, "fake_tuya.json")))["tb_test_2gang"]
    check(f"iHost command 'gang 2 ON' reaches the device ({ms} ms)", r.get("error") == 0 and hw["2"] is True and hw["1"] is False, (r, hw))
    check("iHost state now gang2=on", wait(lambda: ihost_dev(sns["tb_test_2gang"])["state"]["toggle"]["2"]["toggleState"] == "on"))
    r = ih("PUT", "/devices/" + sns["tb_test_bulb"], {"state": {"brightness": {"brightness": 80}}})
    hw = json.load(open(os.path.join(DATA, "fake_tuya.json")))["tb_test_bulb"]
    check("iHost 'brightness 80%' -> bulb on at 802/1000", r.get("error") == 0 and hw["22"] == 802 and hw["20"] is True, (r, hw))
    r = ih("PUT", "/devices/" + sns["tb_test_plug"], {"state": {"power": {"powerState": "off"}}})
    hw = json.load(open(os.path.join(DATA, "fake_tuya.json")))["tb_test_plug"]
    check("iHost 'plug OFF'", r.get("error") == 0 and hw["1"] is False, (r, hw))

    # ---- Tuya -> iHost (someone presses the physical button)
    fake_set("tb_test_2gang", **{"1": True})
    check("physical press on gang 1 reaches the iHost", wait(lambda: ihost_dev(sns["tb_test_2gang"])["state"]["toggle"]["1"]["toggleState"] == "on", 10))
    fake_set("tb_test_plug", **{"1": True})
    check("physical press on plug reaches the iHost", wait(lambda: ihost_dev(sns["tb_test_plug"])["state"]["power"]["powerState"] == "on", 10))

    # ---- offline / back online
    fake_set("tb_test_plug", _offline=True)
    check("unplugged device -> offline in iHost", wait(lambda: ihost_dev(sns["tb_test_plug"])["online"] is False, 10))
    r = ih("PUT", "/devices/" + sns["tb_test_plug"], {"state": {"power": {"powerState": "on"}}})
    check("command to offline device is refused (not faked)", r.get("error") != 0, r)
    fake_set("tb_test_plug", _offline=False)
    check("device back -> online in iHost", wait(lambda: ihost_dev(sns["tb_test_plug"])["online"] is True, 10))

    # ---- bridge restart keeps everything
    proc.terminate(); proc.wait(10)
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "bridge.py")], env=env,
                            stdout=open(os.path.join(DATA, "bridge2.log"), "w"), stderr=subprocess.STDOUT)
    wait(lambda: http("GET", B + "/healthz").get("ok"), 20)
    time.sleep(3)
    r = ih("PUT", "/devices/" + sns["tb_test_2gang"], {"state": {"toggle": {"1": {"toggleState": "off"}}}})
    hw = json.load(open(os.path.join(DATA, "fake_tuya.json")))["tb_test_2gang"]
    check("control still works after bridge restart", r.get("error") == 0 and hw["1"] is False, (r, hw))

    # ---- wrong secret on the control URL is rejected
    bad = http("POST", f"{B}/ihost/wrong/directive", {"directive": {}})
    check("control URL needs the secret", bad.get("error") == "not found", bad)
finally:
    for dev_id, sn in sns.items():
        if sn:
            http("POST", f"{B}/api/devices/{dev_id}/remove", {})
    time.sleep(1)
    left = [d["name"] for d in ih("GET", "/devices")["data"]["device_list"] if (d.get("third_serial_number") or "").startswith("tuya_tb_test")]
    check("cleanup: test devices removed from the iHost", not left, left)
    proc.terminate()
    print("FAILURES:", fails)
    print("--- bridge log ---")
    for name in ("bridge.log", "bridge2.log"):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            print(open(p).read()[-2500:])
