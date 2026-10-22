"""E2E: door lock -> iHost (status sensor + optional unlock switch), against a REAL iHost with a SIMULATED Tuya cloud.
No real lock is contacted. usage: python e2e_lock.py <ihost_url> <token>"""
import json, os, shutil, subprocess, sys, time, urllib.request, urllib.error

IHOST, TOKEN = sys.argv[1].strip().rstrip("/"), sys.argv[2].strip()
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.environ.get("TMPDIR") or HERE, "tb-e2e-lock")
PORT = 8324
B = f"http://127.0.0.1:{PORT}"
LOCK = "tb_test_lock"
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
        time.sleep(0.5)
    return None


def store():
    return json.load(open(os.path.join(DATA, "fake_tuya.json")))


def store_put(fn):
    s = store()
    fn(s)
    json.dump(s, open(os.path.join(DATA, "fake_tuya.json"), "w"))


def cloud_calls():
    return [c["url"] for c in store().get("_cloud", [])]


shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA)
json.dump({"ihost": {"host": IHOST, "token": TOKEN}, "tuya": {"region": "eu", "api_key": "fake", "api_secret": "fake"}},
          open(os.path.join(DATA, "config.json"), "w"))
json.dump({"_cloud_devices": {LOCK: {"lock_motor_state": False, "residual_electricity": 80, "closed_opened": "closed"}}},
          open(os.path.join(DATA, "fake_tuya.json"), "w"))
env = dict(os.environ, BRIDGE_FAKE_TUYA="1", DATA_DIR=DATA, PORT=str(PORT), POLL_SECONDS="1.5", LOCK_POLL_SECONDS="2")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "..", "bridge.py")], env=env,
                        stdout=open(os.path.join(DATA, "bridge.log"), "w"), stderr=subprocess.STDOUT)
sensor = switch = None
try:
    check("bridge starts", wait(lambda: http("GET", B + "/healthz").get("ok"), 20))
    http("POST", B + "/api/import/json", {"devices": json.dumps([{"name": "ZZ Test Door", "id": LOCK, "category": "videolock", "product_name": "Smart Lock"}])})

    # ---- option 1: status sensor
    r = http("POST", f"{B}/api/devices/{LOCK}/add", {})
    sensor = r.get("serial_number")
    check("lock added to iHost as status sensor", bool(sensor), r)
    d = wait(lambda: (lambda x: x if x and (x.get("state") or {}).get("contact") else None)(ihost_dev(sensor)), 10) or ihost_dev(sensor) or {}
    check("iHost: contactSensor, contact=closed (locked), battery 80", d.get("display_category") == "contactSensor"
          and d["state"]["contact"]["contact"] is True and d["state"].get("battery", {}).get("battery") == 80, d.get("state"))
    store_put(lambda s: s["_cloud_devices"][LOCK].update(lock_motor_state=True, residual_electricity=79))
    check("lock unlocked by hand -> iHost sensor 'open' within the poll interval",
          wait(lambda: ihost_dev(sensor)["state"]["contact"]["contact"] is False, 15))
    store_put(lambda s: s["_cloud_devices"][LOCK].update(lock_motor_state=False))
    check("locked again -> sensor 'closed'", wait(lambda: ihost_dev(sensor)["state"]["contact"]["contact"] is True, 15))
    r = ih("PUT", f"/devices/{sensor}", {"state": {"contact": {"contact": False}}})
    check("the sensor itself can't be commanded", r.get("error") != 0 or ihost_dev(sensor)["state"]["contact"]["contact"] is True, r)

    # ---- option 2: unlock switch (guarded)
    r = http("POST", f"{B}/api/devices/{LOCK}/add-unlock", {})
    check("unlock switch refused until allowed", "error" in r, r)
    r = http("POST", f"{B}/api/devices/{LOCK}/unlock-enable", {"enabled": True, "confirm": "yes"})
    check("allowing needs the word UNLOCK", "error" in r, r)
    r = http("POST", f"{B}/api/devices/{LOCK}/unlock-enable", {"enabled": True, "confirm": "UNLOCK"})
    check("allowed after typing UNLOCK", r.get("ok") is True, r)
    r = http("POST", f"{B}/api/devices/{LOCK}/add-unlock", {})
    switch = r.get("serial_number")
    check("unlock switch added to iHost", bool(switch), r)
    sw = wait(lambda: ihost_dev(switch), 5) or {}
    check("iHost: switch named '... Unlock', off, tagged unlock-switch", sw.get("display_category") == "switch" and sw["name"].endswith("Unlock")
          and (sw.get("tags") or {}).get("lock") == "unlock-switch" and sw["state"]["power"]["powerState"] == "off", sw)

    n = len(cloud_calls())
    r = ih("PUT", f"/devices/{switch}", {"state": {"power": {"powerState": "off"}}})
    check("switch OFF does nothing (can't lock remotely)", r.get("error") == 0 and len(cloud_calls()) == n, r)
    t0 = time.time()
    r = ih("PUT", f"/devices/{switch}", {"state": {"power": {"powerState": "on"}}})
    ms = int((time.time() - t0) * 1000)
    check(f"switch ON accepted by iHost within its 3 s limit ({ms} ms)", r.get("error") == 0 and ms < 3000, r)
    check("cloud got password-ticket then open-door", wait(lambda: any(u.endswith("open-door") for u in cloud_calls()[n:]), 8)
          and [u.rsplit("/", 1)[-1] for u in cloud_calls()[n:] if "ticket" in u or "open-door" in u] == ["password-ticket", "open-door"], cloud_calls()[n:])
    check("switch falls back to OFF by itself", wait(lambda: ihost_dev(switch)["state"]["power"]["powerState"] == "off", 10))
    check("sensor shows UNLOCKED after the unlock", wait(lambda: ihost_dev(sensor)["state"]["contact"]["contact"] is False, 15))
    lu = next(x for x in http("GET", B + "/api/status")["devices"] if x["id"] == LOCK).get("last_unlock") or {}
    check("bridge page shows last iHost unlock OK", lu.get("ok") is True, lu)

    # rate limit: 3 per 10 minutes
    results = []
    for _ in range(3):
        time.sleep(2.5)
        results.append(ih("PUT", f"/devices/{switch}", {"state": {"power": {"powerState": "on"}}}).get("error"))
    check("rate limit: 3 unlocks per 10 min, the 4th is refused", results[:2] == [0, 0] and results[2] != 0, results)

    # cloud outage -> sensor offline, back -> online
    store_put(lambda s: s.update(_cloud_down=True))
    check("Tuya cloud unreachable 3x -> sensor offline in iHost", wait(lambda: ihost_dev(sensor)["online"] is False, 20))
    store_put(lambda s: s.update(_cloud_down=False))
    check("cloud back -> sensor online", wait(lambda: ihost_dev(sensor)["online"] is True, 15))

    # disallow removes the switch
    r = http("POST", f"{B}/api/devices/{LOCK}/unlock-enable", {"enabled": False})
    check("Disallow removes the unlock switch from the iHost", r.get("ok") and wait(lambda: ihost_dev(switch) is None, 8))
    switch = None
    st = next(x for x in http("GET", B + "/api/status")["devices"] if x["id"] == LOCK)
    check("status API: locked/battery/flags", st["unlock_enabled"] is False and st["unlock_in_ihost"] is False and st["battery"] == 79, st)
finally:
    if switch:
        http("POST", f"{B}/api/devices/{LOCK}/remove-unlock", {})
    if sensor:
        http("POST", f"{B}/api/devices/{LOCK}/remove", {})
    time.sleep(1)
    left = [d["name"] for d in ih("GET", "/devices")["data"]["device_list"] if (d.get("third_serial_number") or "").startswith("tuya_tb_test")]
    check("cleanup: test devices removed from the iHost", not left, left)
    proc.terminate()
    print("FAILURES:", fails)
    print(open(os.path.join(DATA, "bridge.log")).read()[-2200:])
