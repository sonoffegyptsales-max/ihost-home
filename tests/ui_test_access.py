"""Headless-Chrome tests for multi-iHost, add-iHost scan, login and internet access.

Needs: server running with >=2 paired iHosts (mocks), a password set, and internet access on.
  python tests/ui_test_access.py http://127.0.0.1:8099 <password> ./shots
"""
import base64, json, os, subprocess, sys, time, urllib.request
import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
APP, PASSWORD = sys.argv[1], sys.argv[2]
OUT = sys.argv[3] if len(sys.argv) > 3 else "shots"
os.makedirs(OUT, exist_ok=True)
PORT = 9336
proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={OUT}/prof-access",
                         "--remote-allow-origins=*", "--no-first-run", "--hide-scrollbars", "about:blank"])
fails = 0
try:
    for _ in range(50):
        try:
            page = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json")) if t["type"] == "page"); break
        except Exception:
            time.sleep(0.2)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
    mid = [0]; errors = []

    def cdp(m, **p):
        mid[0] += 1; ws.send(json.dumps({"id": mid[0], "method": m, "params": p}))
        while True:
            r = json.loads(ws.recv())
            if r.get("method") == "Runtime.exceptionThrown":
                errors.append(r["params"]["exceptionDetails"].get("exception", {}).get("description", "exception"))
            if r.get("id") == mid[0]:
                if "error" in r: raise RuntimeError(r["error"])
                return r.get("result", {})

    def js(e):
        r = cdp("Runtime.evaluate", expression=e, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r: raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    def shot(n):
        time.sleep(0.6)
        open(f"{OUT}/{n}.png", "wb").write(base64.b64decode(cdp("Page.captureScreenshot", format="png")["data"]))
        print("shot", n)

    def check(label, cond):
        global fails
        print(("PASS " if cond else "FAIL ") + label)
        fails += 0 if cond else 1

    def wait_for(expr, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if js(expr): return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    sec = json.load(urllib.request.urlopen(APP + "/api/security"))
    tunnel = sec["remote"]["url"]

    cdp("Runtime.enable"); cdp("Page.enable"); cdp("Network.enable")
    cdp("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])

    # ---------- LAN: home switcher
    cdp("Page.navigate", url=APP)
    check("app loads on LAN without password", wait_for("document.querySelectorAll('.tile').length > 0"))
    first = js("document.querySelector('#homeTitle').textContent")
    js("document.querySelector('#homeTitle').click()")
    check("homes sheet lists 2 homes", wait_for("document.querySelectorAll('[data-hub]').length === 2"))
    shot("a1-homes-sheet")
    js("[...document.querySelectorAll('[data-hub]')].find(b => !b.querySelector('.check')).click()")
    check("switched home", wait_for(f"document.querySelector('#homeTitle').textContent !== {json.dumps(first)} && document.querySelectorAll('.tile').length > 0"))
    second = js("document.querySelector('#homeTitle').textContent")
    print("   homes:", first, "->", second)
    check("choice remembered after reload", (cdp("Page.reload") or True) and wait_for(f"document.querySelector('#homeTitle').textContent === {json.dumps(second)}"))

    # ---------- settings: access + internet access sheet
    js("document.querySelector('#settingsBtn').click()")
    check("settings shows Internet Access On", wait_for("[...document.querySelectorAll('[data-act=remote] .val')].some(v => v.textContent === 'On')"))
    shot("a2-settings")
    js("document.querySelector('[data-act=remote]').click()")
    check("internet sheet shows the public link", wait_for(f"document.querySelector('#sheetInner').textContent.includes({json.dumps(tunnel)})"))
    js("document.querySelector('[data-act=make-link]').click()")
    check("sign-in link created and shown", wait_for("document.querySelector('#sheetInner').textContent.includes('/login?key=')"))
    link = js("[...document.querySelectorAll('#sheetInner .lbl.mono')].map(e => e.textContent).find(t => t.includes('/login?key='))")
    shot("a3-internet-access")
    js("document.querySelector('[data-act=close]').click()")

    # ---------- add iHost: real LAN scan (read-only), then cancel
    js("document.querySelector('#homeTitle').click()")
    wait_for("document.querySelector('[data-act=add-hub]')")
    js("document.querySelector('[data-act=add-hub]').click()")
    check("scan lists an iHost on the network", wait_for("document.querySelectorAll('[data-pick-ip]').length > 0", 40))
    print("   found:", js("[...document.querySelectorAll('[data-pick-ip]')].map(b => b.dataset.pickIp)"))
    js("document.querySelector('[data-pick-ip]').click()")
    check("tapping a result fills the IP", js("document.querySelector('#hostInput').value") != "")
    shot("a4-add-ihost")
    js("document.querySelector('#cancelAdd').click()")
    check("cancel returns to the home", wait_for("document.querySelectorAll('.tile').length > 0"))

    # ---------- simulated internet request (Cloudflare headers) -> login page
    cdp("Network.setExtraHTTPHeaders", headers={"CF-Connecting-IP": "198.51.100.7", "Cf-Ray": "test"})
    cdp("Network.clearBrowserCookies")
    cdp("Page.navigate", url=APP)
    check("remote visitor gets the sign-in page", wait_for("!!document.querySelector('#pwLogin')"))
    shot("a5-login")
    js("document.querySelector('#pwLogin').value = 'wrong-password'; document.querySelector('#loginForm').requestSubmit()")
    check("wrong password rejected", wait_for("document.querySelector('#content').textContent.includes('Wrong password')"))
    js(f"document.querySelector('#pwLogin').value = {json.dumps(PASSWORD)}; document.querySelector('#loginForm').requestSubmit()")
    check("right password opens the home", wait_for("document.querySelectorAll('.tile').length > 0"))
    js("document.querySelector('#settingsBtn').click()")
    wait_for("document.querySelector('[data-act=remote]')")
    check("remote session hides pair/remove", js("!document.querySelector('[data-act=repair]') && !document.querySelector('[data-act=remove-hub]')"))
    js("document.querySelector('[data-act=signout]').click()")
    check("sign out returns to login", wait_for("!!document.querySelector('#pwLogin')"))
    cdp("Network.setExtraHTTPHeaders", headers={})

    # ---------- REAL internet: through the Cloudflare tunnel
    if tunnel:
        cdp("Network.clearBrowserCookies")
        cdp("Page.navigate", url=tunnel)
        check("tunnel: sign-in page over HTTPS", wait_for("!!document.querySelector('#pwLogin')", 30))
        js(f"document.querySelector('#pwLogin').value = {json.dumps(PASSWORD)}; document.querySelector('#loginForm').requestSubmit()")
        check("tunnel: home loads after login", wait_for("document.querySelectorAll('.tile').length > 0", 30))
        check("tunnel: falls back to 3s refresh", wait_for("document.querySelector('#liveDot').classList.contains('poll')", 15))
        key = js("document.querySelector('.tile[aria-pressed]').dataset.key")
        before = js(f"document.querySelector('.tile[data-key=\"{key}\"]').getAttribute('aria-pressed')")
        js(f"document.querySelector('.tile[data-key=\"{key}\"]').click()")
        check("tunnel: tap toggles a device", wait_for(f"document.querySelector('.tile[data-key=\"{key}\"]').getAttribute('aria-pressed') !== {json.dumps(before)} && !document.querySelector('.tile.busy')", 20))
        shot("a6-over-internet")
        # sign-in link on a fresh browser session
        cdp("Network.clearBrowserCookies")
        cdp("Page.navigate", url=link)
        check("tunnel: sign-in link logs straight in", wait_for("document.querySelectorAll('.tile').length > 0", 30))
    else:
        print("SKIP tunnel checks (internet access not running)")

    check(f"no JS errors {errors}", not errors)
    print("FAILURES:", fails)
finally:
    proc.terminate()
