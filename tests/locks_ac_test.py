"""Cloud version: door locks (Tuya cloud) + AC tile/sheet, against mock iHost + mock Tuya (signature-checking).
API checks with urllib, UI checks with headless Chrome. Screenshots go to argv[1]."""
import base64, json, os, subprocess, sys, time, urllib.request, urllib.error
import websocket

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "shots")
os.makedirs(OUT, exist_ok=True)
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
APPPW, UNLOCKPW = "app-password-123", "door-4321"
B = "http://127.0.0.1:18099"
fails = 0
procs = []


def check(label, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + label + (f"  [{extra}]" if extra and not ok else ""), flush=True)
    fails += 0 if ok else 1


cookie = {"v": ""}


def http(method, path, body=None, base=B):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if cookie["v"]:
        req.add_header("Cookie", cookie["v"])
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            sc = r.headers.get("Set-Cookie")
            if sc:
                cookie["v"] = sc.split(";")[0]
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def start(cmd, env=None, name="p"):
    p = subprocess.Popen(cmd, cwd=ROOT, env=dict(os.environ, **(env or {})), stdout=open(os.path.join(OUT, name + ".log"), "w"), stderr=subprocess.STDOUT)
    procs.append(p)
    return p


try:
    start([sys.executable, "-u", "tests/mock_ihost.py", "18775"], {"MOCK_AC": "1"}, "mock_ihost")
    start(["node.exe", "tests/mock_tuya.mjs", "18767"], name="mock_tuya")
    for url in ("http://127.0.0.1:18775/open-api/v2/rest/bridge", "http://127.0.0.1:18767/__calls"):
        for _ in range(80):
            try:
                urllib.request.urlopen(url, timeout=2).read()
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(0.25)
    env = {"APP_PASSWORD": APPPW, "IHOST_URL": "http://127.0.0.1:18775", "IHOST_TOKEN": "test-token-123", "IHOST_NAME": "Test Home",
           "TUYA_ENDPOINT": "http://127.0.0.1:18767", "TUYA_ACCESS_ID": "mockid", "TUYA_ACCESS_SECRET": "mocksecret",
           "TUYA_LOCKS": "lockdev1", "UNLOCK_PASSWORD": UNLOCKPW}
    start(["node.exe", "tests/vercel_local.mjs", "18099"], env, "vercel")
    for _ in range(60):
        try:
            if http("GET", "/api/session")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    # ---------------- API
    st, s = http("GET", "/api/session")
    check("session advertises locks", s.get("locks") is True, s)
    st, r = http("GET", "/api/locks")
    check("locks need app login", st == 401, st)
    http("POST", "/api/login", {"password": APPPW})
    st, r = http("GET", "/api/locks")
    lock = (r.get("locks") or [{}])[0]
    check("lock list via signed Tuya calls", st == 200 and lock.get("name") == "Front Door" and lock["status"].get("residual_electricity") == 87, r)
    st, r = http("GET", "/api/locks/otherdevice")
    check("only configured locks are reachable", st == 404, st)
    st, d = http("GET", "/api/locks/lockdev1")
    check("detail: history + alarms + remote-unlock setting", st == 200 and len(d.get("history") or []) == 2 and len(d.get("alarms") or []) == 1
          and d.get("remote_unlock") and d["remote_unlock"][0]["open"] is True, d)
    st, r = http("POST", "/api/locks/lockdev1/unlock", {"password": "wrong"})
    check("wrong unlock password refused", st == 403 and "Wrong" in r.get("error", ""), (st, r))
    st, r = http("POST", "/api/locks/lockdev1/unlock", {"password": APPPW})
    check("app password is NOT accepted as unlock password", st == 403, (st, r))
    _, calls = http("GET", "/__calls", base="http://127.0.0.1:18767")
    check("no unlock reached Tuya after wrong passwords", not any("open-door" in c["path"] for c in calls))
    # ---------------- UI (headless Chrome)
    chrome = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=19341", f"--user-data-dir={OUT}/prof-locks",
                               "--remote-allow-origins=*", "--no-first-run", "--hide-scrollbars", "about:blank"])
    procs.append(chrome)
    for _ in range(50):
        try:
            page = next(t for t in json.load(urllib.request.urlopen("http://127.0.0.1:19341/json")) if t["type"] == "page"); break
        except Exception:
            time.sleep(0.2)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30); mid = [0]; errors = []
    def cdp(m, **p):
        mid[0] += 1; ws.send(json.dumps({"id": mid[0], "method": m, "params": p}))
        while True:
            x = json.loads(ws.recv())
            if x.get("method") == "Runtime.exceptionThrown": errors.append(x["params"]["exceptionDetails"].get("exception", {}).get("description"))
            if x.get("method") == "Page.javascriptDialogOpening": cdp_dialog.append(x["params"]["message"])
            if x.get("id") == mid[0]: return x.get("result", {})
    cdp_dialog = []
    def js(e): return cdp("Runtime.evaluate", expression=e, returnByValue=True, awaitPromise=True)["result"].get("value")
    def wait(e, t=15):
        end = time.time() + t
        while time.time() < end:
            try:
                if js(e): return True
            except Exception: pass
            time.sleep(0.25)
        return False
    def shot(n): time.sleep(0.5); open(f"{OUT}/{n}.png", "wb").write(base64.b64decode(cdp("Page.captureScreenshot", format="png")["data"])); print("shot", n)
    cdp("Runtime.enable"); cdp("Page.enable")
    cdp("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
    cdp("Page.navigate", url=B)
    wait("!!document.querySelector('#pwLogin')")
    js(f"document.querySelector('#pwLogin').value = {json.dumps(APPPW)}; document.querySelector('#loginForm').requestSubmit()")
    check("UI: lock tile shown", wait("!!document.querySelector('[data-lock=lockdev1]')", 20))
    check("UI: lock tile says Locked · 87%", "Locked" in (js("document.querySelector('[data-lock=lockdev1]').textContent") or "")
          and "87%" in js("document.querySelector('[data-lock=lockdev1]').textContent"))
    check("UI: AC tile shows Off", wait("[...document.querySelectorAll('.tile')].some(t => t.textContent.includes('Living AC') && t.textContent.includes('Off'))"))
    shot("l1-home")
    tiles = js("[...document.querySelectorAll('.tile')].map(t => t.textContent.replace(/\\s+/g, ' ').trim())")
    check("UI: iHost unlock switch is NOT shown as a tile", not any("Unlock" in t and "Front Door Lock" in t for t in tiles), tiles)
    check("UI: lock status sensor tile says Locked", any("Front Door Lock" in t and "Locked" in t for t in tiles), tiles)
    hub_id = http("GET", "/api/hubs")[1]["hubs"][0]["id"]
    st, r = http("PUT", f"/api/hubs/{hub_id}/devices/SN-UNLOCKSW", {"state": {"power": {"powerState": "on"}}})
    check("API refuses to flip the unlock switch (403)", st == 403, (st, r))
    # AC sheet
    js("[...document.querySelectorAll('.tile')].find(t => t.textContent.includes('Living AC')).dispatchEvent(new MouseEvent('contextmenu', {bubbles: true}))")
    check("UI: AC sheet has mode/temp/fan", wait("document.querySelectorAll('[data-ac-mode]').length === 5 && !!document.querySelector('[data-ac-temp=\"1\"]') && document.querySelectorAll('[data-ac-fan]').length === 4"))
    js("document.querySelector('[data-act=toggle]').click()"); time.sleep(0.8)
    js("document.querySelector('[data-ac-temp=\"1\"]').click()"); time.sleep(0.8)
    js("document.querySelector('[data-ac-mode=heat]').click()"); time.sleep(0.8)
    js("document.querySelector('[data-ac-fan=high]').click()"); time.sleep(0.8)
    st, devs = http("GET", "/api/hubs/" + http("GET", "/api/hubs")[1]["hubs"][0]["id"] + "/devices")
    ac = next(x for x in devs["devices"] if x["serial_number"] == "SN-AC")["state"]
    check("UI: AC commands reached the iHost (on, 25°, heat, high)", ac["power"]["powerState"] == "on"
          and ac["thermostat-target-setpoint"]["manual-mode"]["targetSetpoint"] == 25
          and ac["mode"]["airConditionerMode"]["modeValue"] == "heat" and ac["mode"]["fanLevel"]["modeValue"] == "high", ac)
    check("UI: AC sheet shows 25°C + Heat", "25°C" in js("document.querySelector('#sheetInner').textContent")
          and js("document.querySelector('[data-ac-mode=heat]').getAttribute('aria-pressed')") == "true")
    shot("l2-ac-sheet")
    js("document.querySelector('[data-act=close]').click()"); time.sleep(0.5)
    # lock sheet
    js("document.querySelector('[data-lock=lockdev1]').click()")
    check("UI: lock sheet with history loaded", wait("document.querySelector('#sheetInner').textContent.includes('Fingerprint') && document.querySelector('#sheetInner').textContent.includes('Magdy')"))
    txt = js("document.querySelector('#sheetInner').textContent")
    check("UI: lock sheet shows battery, door, alarm, remote setting, raw codes",
          all(s in txt for s in ["87%", "Closed", "Alarm: wrong finger", "Allowed", "beep_volume"]), txt[:400])
    shot("l3-lock-sheet")
    # unlock without password -> refused locally
    js("window.confirm = () => true")
    js("document.querySelector('[data-act=unlock]').click()"); time.sleep(0.6)
    _, calls = http("GET", "/__calls", base="http://127.0.0.1:18767")
    check("UI: empty password doesn't call the server", not any("open-door" in c["path"] for c in calls))
    js("document.querySelector('#unlockPw').value = 'nope'; document.querySelector('[data-act=unlock]').click()")
    check("UI: wrong password shows error", wait("document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('Wrong unlock password')", 8))
    js(f"document.querySelector('#unlockPw').value = {json.dumps(UNLOCKPW)}; document.querySelector('[data-act=unlock]').click()")
    check("UI: correct password unlocks", wait("document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('unlocked')", 10))
    _, calls = http("GET", "/__calls", base="http://127.0.0.1:18767")
    check("Tuya got password-ticket then open-door with that ticket",
          [c["path"].rsplit("/", 1)[-1] for c in calls if "ticket" in c["path"] or "open-door" in c["path"]] == ["password-ticket", "open-door"]
          and json.loads([c for c in calls if "open-door" in c["path"]][0]["body"]) == {"ticket_id": "tk1"})
    check("UI: password field cleared after unlock", wait("document.querySelector('#unlockPw') && document.querySelector('#unlockPw').value === ''", 5))
    check("UI: lock shows Unlocked after refresh", wait("document.querySelector('#sheetInner').textContent.includes('Unlocked')", 10))
    shot("l4-unlocked")
    # brute force limit (API)
    codes = [http("POST", "/api/locks/lockdev1/unlock", {"password": f"bad{i}"})[0] for i in range(6)]
    check("5 wrong passwords -> blocked (429), even with the right one", codes[-1] == 429 and http("POST", "/api/locks/lockdev1/unlock", {"password": UNLOCKPW})[0] == 429, codes)
    check(f"no JS errors {errors}", not errors)
finally:
    for p in procs:
        p.terminate()
    print("FAILURES:", fails)
