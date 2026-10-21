"""Read-only screenshots of the live app (no clicks that change devices)."""
import base64, json, os, subprocess, sys, time, urllib.request
import websocket
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
APP, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9335", f"--user-data-dir={OUT}/prof3",
                         "--remote-allow-origins=*", "--no-first-run", "--hide-scrollbars", "about:blank"])
try:
    for _ in range(50):
        try:
            page = next(t for t in json.load(urllib.request.urlopen("http://127.0.0.1:9335/json")) if t["type"] == "page"); break
        except Exception: time.sleep(0.2)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20); mid = [0]; errs = []
    def cdp(m, **p):
        mid[0] += 1; ws.send(json.dumps({"id": mid[0], "method": m, "params": p}))
        while True:
            r = json.loads(ws.recv())
            if r.get("method") == "Runtime.exceptionThrown": errs.append(r["params"]["exceptionDetails"].get("text"))
            if r.get("id") == mid[0]: return r.get("result", {})
    def js(e): return cdp("Runtime.evaluate", expression=e, returnByValue=True)["result"].get("value")
    def shot(n):
        time.sleep(0.6); open(f"{OUT}/{n}.png", "wb").write(base64.b64decode(cdp("Page.captureScreenshot", format="png")["data"])); print("shot", n)
    cdp("Runtime.enable")
    cdp("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
    cdp("Page.navigate", url=APP); time.sleep(3)
    print("tiles", js("document.querySelectorAll('.tile').length"), "live", js("document.querySelector('#liveDot').className"))
    shot("live-phone")
    # open detail sheet of the first T5 channel via contextmenu (opens details only, no toggle)
    js("document.querySelector('.tile[data-key^=\"cf91ec2c\"]').dispatchEvent(new MouseEvent('contextmenu', {bubbles:true}))")
    shot("live-phone-sheet")
    js("document.querySelector('[data-act=\"close\"]').click()"); time.sleep(0.5)
    cdp("Emulation.setDeviceMetricsOverride", width=1280, height=820, deviceScaleFactor=1, mobile=False)
    shot("live-desktop")
    print("errors", errs)
finally:
    proc.terminate()
