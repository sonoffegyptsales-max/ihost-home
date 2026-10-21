"""Phone-style end-to-end: PIN change (with tap-outside), PIN->password upgrade from the Internet Access
switch, turning internet access on, then signing in through the real public link."""
import base64, json, os, subprocess, sys, time, urllib.request
import websocket
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
APP, OUT, CONFIG = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9338", f"--user-data-dir={OUT}/prof-pin",
                         "--remote-allow-origins=*", "--no-first-run", "about:blank"])
fails = 0
try:
    for _ in range(50):
        try:
            page = next(t for t in json.load(urllib.request.urlopen("http://127.0.0.1:9338/json")) if t["type"] == "page"); break
        except Exception: time.sleep(0.2)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30); mid = [0]; errors = []
    def cdp(m, **p):
        mid[0] += 1; ws.send(json.dumps({"id": mid[0], "method": m, "params": p}))
        while True:
            r = json.loads(ws.recv())
            if r.get("method") == "Runtime.exceptionThrown": errors.append(r["params"]["exceptionDetails"].get("exception", {}).get("description"))
            if r.get("id") == mid[0]: return r.get("result", {})
    def js(e): return cdp("Runtime.evaluate", expression=e, returnByValue=True, awaitPromise=True)["result"].get("value")
    def wait(e, t=12):
        end = time.time() + t
        while time.time() < end:
            try:
                if js(e): return True
            except Exception: pass
            time.sleep(0.25)
        return False
    def tap(sel):
        x, y = js(f"(() => {{ const el = document.querySelector({json.dumps(sel)}); el.scrollIntoView({{block:'center'}}); const r = el.getBoundingClientRect(); return [r.x + r.width/2, r.y + r.height/2]; }})()")
        cdp("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}]); time.sleep(0.08)
        cdp("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[]); time.sleep(0.35)
    def typ(t): cdp("Input.insertText", text=t); time.sleep(0.15)
    def shot(n): open(f"{OUT}/{n}.png", "wb").write(base64.b64decode(cdp("Page.captureScreenshot", format="png")["data"])); print("shot", n)
    def auth(): return json.load(open(CONFIG, encoding="utf-8"))["auth"]
    def check(label, ok):
        global fails
        print(("PASS " if ok else "FAIL ") + label); fails += 0 if ok else 1
    def change(cur, new, tap_outside=False):
        tap("[data-act=password]"); wait("!!document.querySelector('#pwNew')")
        tap("#pwCurrent"); typ(cur); tap("#pwNew"); typ(new); tap("#pwNew2"); typ(new)
        if tap_outside:
            tap(".sheet-head .titles"); time.sleep(0.4)
        tap("form[data-form=password] button[type=submit]"); time.sleep(1.5)

    cdp("Runtime.enable")
    cdp("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    cdp("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
    cdp("Page.navigate", url=APP)
    wait("!!document.querySelector('#pwLogin')"); tap("#pwLogin"); typ("1234"); tap("#loginForm button")
    check("login with old PIN 1234", wait("document.querySelectorAll('.tile').length > 0"))
    tap("#settingsBtn"); wait("!!document.querySelector('[data-act=password]')")

    change("1234", "5678", tap_outside=True)
    check("change PIN 1234 -> 5678 (with tap outside first)", js("document.querySelector('#toast').textContent").startswith("PIN saved") and auth()["legacy_pin"] is True)

    change("0000", "9999")
    check("wrong current PIN shows error in the form", "wrong" in (js("document.querySelector('#pwErr') && document.querySelector('#pwErr').textContent") or "").lower())
    shot("p1-wrong-current")
    js("document.querySelector('[data-act=back]').click()"); time.sleep(0.4)

    # try to turn on internet access with a PIN -> guided to the password form
    tap("[data-act=remote]"); wait("!!document.querySelector('[data-act=remote-toggle]')")
    check("internet switch is tappable (not disabled)", js("!document.querySelector('[data-act=remote-toggle]').disabled"))
    tap("[data-act=remote-toggle]")
    check("PIN too short -> sent to password form", wait("!!document.querySelector('#pwNew')"))
    shot("p2-upgrade-prompt")
    tap("#pwCurrent"); typ("5678"); tap("#pwNew"); typ("Train-Home-2026"); tap("#pwNew2"); typ("Train-Home-2026")
    tap(".sheet-head .titles"); time.sleep(0.3)
    tap("form[data-form=password] button[type=submit]")
    check("password saved and back on Internet Access", wait("!!document.querySelector('[data-act=remote-toggle]')") and auth()["legacy_pin"] is False)
    tap("[data-act=remote-toggle]")
    check("internet access connects and shows a link", wait("document.querySelector('#sheetInner').textContent.includes('trycloudflare.com')", 120))
    url = js("[...document.querySelectorAll('#sheetInner .lbl.mono')].map(e => e.textContent).find(t => t.includes('trycloudflare.com'))")
    print("   link:", url)
    shot("p3-internet-on")

    # fresh browser session over the real internet
    cdp("Network.enable"); cdp("Network.clearBrowserCookies")
    time.sleep(5)
    cdp("Page.navigate", url=url)
    check("public link opens the sign-in page", wait("!!document.querySelector('#pwLogin')", 40))
    tap("#pwLogin"); typ("Train-Home-2026"); tap("#loginForm button")
    check("signed in over the internet, home shown", wait("document.querySelectorAll('.tile').length > 0", 30))
    shot("p4-over-internet")
    check(f"no JS errors {errors}", not errors)
    print("FAILURES:", fails)
finally:
    proc.terminate()
