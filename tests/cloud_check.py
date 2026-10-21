"""Read-only browser check of the deployed cloud version: sign in, tiles, live dot, settings."""
import base64, json, os, subprocess, sys, time, urllib.request
import websocket
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
URL, PASSWORD, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9339", f"--user-data-dir={OUT}/prof-cloud",
                         "--remote-allow-origins=*", "--no-first-run", "--hide-scrollbars", "about:blank"])
fails = 0
try:
    for _ in range(50):
        try:
            page = next(t for t in json.load(urllib.request.urlopen("http://127.0.0.1:9339/json")) if t["type"] == "page"); break
        except Exception: time.sleep(0.2)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30); mid = [0]; errors = []
    def cdp(m, **p):
        mid[0] += 1; ws.send(json.dumps({"id": mid[0], "method": m, "params": p}))
        while True:
            r = json.loads(ws.recv())
            if r.get("method") == "Runtime.exceptionThrown": errors.append(r["params"]["exceptionDetails"].get("exception", {}).get("description"))
            if r.get("id") == mid[0]: return r.get("result", {})
    def js(e): return cdp("Runtime.evaluate", expression=e, returnByValue=True, awaitPromise=True)["result"].get("value")
    def wait(e, t=20):
        end = time.time() + t
        while time.time() < end:
            try:
                if js(e): return True
            except Exception: pass
            time.sleep(0.3)
        return False
    def shot(n): time.sleep(0.6); open(f"{OUT}/{n}.png", "wb").write(base64.b64decode(cdp("Page.captureScreenshot", format="png")["data"])); print("shot", n)
    def check(label, ok):
        global fails
        print(("PASS " if ok else "FAIL ") + label); fails += 0 if ok else 1
    cdp("Runtime.enable")
    cdp("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
    cdp("Page.navigate", url=URL)
    check("sign-in page", wait("!!document.querySelector('#pwLogin')"))
    shot("c1-login")
    js(f"document.querySelector('#pwLogin').value = {json.dumps(PASSWORD)}; document.querySelector('#loginForm').requestSubmit()")
    check("home with real devices", wait("document.querySelectorAll('.tile').length >= 10", 30))
    print("   title:", js("document.querySelector('#homeTitle').textContent"), "| tiles:", js("document.querySelectorAll('.tile').length"))
    check("live updates connected (green)", wait("document.querySelector('#liveDot').classList.contains('on')", 15))
    shot("c2-home")
    js("document.querySelector('#settingsBtn').click()")
    check("cloud settings shown", wait("document.querySelector('#sheetInner').textContent.includes('eWeLink remote')"))
    check("home-only actions hidden", js("!document.querySelector('[data-act=repair]') && !document.querySelector('[data-act=remote]')"))
    shot("c3-settings")
    check(f"no JS errors {errors}", not errors)
    print("FAILURES:", fails)
finally:
    proc.terminate()
