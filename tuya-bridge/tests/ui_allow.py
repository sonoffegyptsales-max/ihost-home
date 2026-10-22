"""Browser check of the bridge page's 'Allow unlock' dialog (simulated cloud, local test bridge, no iHost writes)."""
import base64, json, os, shutil, subprocess, sys, time, urllib.request
import websocket
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.argv[1]  # python with tinytuya (bridge venv)
OUT = os.path.join(os.environ.get("TMPDIR") or HERE, "tb-ui-allow")
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
json.dump({"ihost": {"host": "http://127.0.0.1:9", "token": ""}, "tuya": {"region": "eu", "api_key": "fake", "api_secret": "fake"},
           "devices": {"L1": {"id": "L1", "name": "Smart Door Lock", "category": "videolock", "product_name": "Smart Lock",
                              "lock_status": {"lock_motor_state": False, "residual_electricity": 68}, "lock_checked": time.time(),
                              "last_unlocked": {"at": time.time() - 300, "method": "fingerprint"}}}}, open(f"{OUT}/config.json", "w"))
br = subprocess.Popen([PY, os.path.join(HERE, "..", "bridge.py")], env=dict(os.environ, BRIDGE_FAKE_TUYA="1", DATA_DIR=OUT, PORT="8325"),
                      stdout=open(f"{OUT}/bridge.log", "w"), stderr=subprocess.STDOUT)
ch = subprocess.Popen([r"C:\Program Files\Google\Chrome\Application\chrome.exe", "--headless=new", "--remote-debugging-port=19343",
                       f"--user-data-dir={OUT}/prof", "--remote-allow-origins=*", "--no-first-run", "--hide-scrollbars", "about:blank"])
fails = 0
def check(l, ok, x=""):
    global fails; print(("PASS " if ok else "FAIL ") + l + (f" [{x}]" if x and not ok else "")); fails += 0 if ok else 1
try:
    time.sleep(2.5)
    page = next(t for t in json.load(urllib.request.urlopen("http://127.0.0.1:19343/json")) if t["type"] == "page")
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20); mid = [0]
    def cdp(m, **p):
        mid[0] += 1; ws.send(json.dumps({"id": mid[0], "method": m, "params": p}))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == mid[0]: return r.get("result", {})
    js = lambda e: cdp("Runtime.evaluate", expression=e, returnByValue=True, awaitPromise=True)["result"].get("value")
    def wait(e, t=10):
        end = time.time() + t
        while time.time() < end:
            if js(e): return True
            time.sleep(0.25)
        return False
    cdp("Emulation.setDeviceMetricsOverride", width=420, height=900, deviceScaleFactor=2, mobile=True)
    cdp("Page.navigate", url="http://127.0.0.1:8325/")
    check("lock row shows last unlocked (fingerprint)", wait("document.body.textContent.includes('Last unlocked') && document.body.textContent.includes('fingerprint')"))
    js("document.querySelector('[data-act=allow-unlock]').click()")
    check("Allow opens a dialog (no browser prompt)", wait("document.querySelector('#allowDlg').open"))
    open(f"{OUT}/allow.png", "wb").write(base64.b64decode(cdp("Page.captureScreenshot", format="png")["data"]))
    js("document.querySelector('#aWord').value='yes'; document.querySelector('#allowForm').requestSubmit()")
    check("wrong word is rejected in the dialog", wait("document.querySelector('#aMsg').textContent.includes('UNLOCK') && document.querySelector('#allowDlg').open"))
    js("document.querySelector('#aWord').value='unlock'; document.querySelector('#allowForm').requestSubmit()")
    check("typing UNLOCK allows it and closes the dialog", wait("!document.querySelector('#allowDlg').open && !!document.querySelector('[data-act=add-unlock]')"))
    st = json.load(urllib.request.urlopen("http://127.0.0.1:8325/api/status"))["devices"][0]
    check("bridge saved unlock_enabled", st["unlock_enabled"] is True, st)
    print("FAILURES:", fails, "| shot:", f"{OUT}/allow.png")
finally:
    ch.terminate(); br.terminate()
