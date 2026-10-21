"""Headless-Chrome UI test for iHost Home (against the mock gateway). Uses its own Chrome profile/port."""
import base64, json, os, subprocess, sys, time, urllib.request
import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
APP = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8099"
MOCK = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8765"
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.environ.get("TMPDIR", "."), "ihh-shots")
os.makedirs(OUT, exist_ok=True)
PORT = 9333
prof = os.path.join(OUT, "profile")
proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                         "--remote-allow-origins=*", "--no-first-run", "--disable-gpu", "--hide-scrollbars", "about:blank"])
try:
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
            page = next(t for t in tabs if t["type"] == "page")
            break
        except Exception:
            time.sleep(0.2)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20)
    mid = [0]
    errors = []

    def cdp(method, **params):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("method") == "Runtime.exceptionThrown":
                errors.append(m["params"]["exceptionDetails"].get("exception", {}).get("description", "exception"))
            if m.get("method") == "Runtime.consoleAPICalled" and m["params"]["type"] == "error":
                errors.append(" ".join(str(a.get("value", a.get("description"))) for a in m["params"]["args"]))
            if m.get("id") == mid[0]:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    def js(expr):
        r = cdp("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    def shot(name):
        time.sleep(0.5)
        data = cdp("Page.captureScreenshot", format="png")["data"]
        p = os.path.join(OUT, name + ".png")
        open(p, "wb").write(base64.b64decode(data))
        print("shot", p)

    def mock_dev(sn):
        devs = json.load(urllib.request.urlopen(urllib.request.Request(
            MOCK + "/open-api/v2/rest/devices", headers={"Authorization": "Bearer test-token-123"})))["data"]["device_list"]
        return next(d for d in devs if d["serial_number"] == sn)

    def center(sel):
        return js(f"(() => {{ const r = document.querySelector({json.dumps(sel)}).getBoundingClientRect(); return [r.x + r.width/2, r.y + r.height/2]; }})()")

    def tap(sel, hold=0.05):
        x, y = center(sel)
        cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        time.sleep(hold)
        cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            check.fails += 1
    check.fails = 0

    cdp("Runtime.enable"); cdp("Page.enable")
    # ---- phone, dark
    cdp("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
    cdp("Page.navigate", url=APP)
    time.sleep(2.5)
    n_tiles = js("document.querySelectorAll('.tile').length")
    check(f"tiles rendered ({n_tiles})", n_tiles >= 6)
    check("live dot connected", js("document.querySelector('#liveDot').classList.contains('on')"))
    shot("1-phone-dark-home")

    # tap toggles the Kitchen plug (whatever its name is now) -> mock state flips
    before = mock_dev("SN-PLUG-1")["state"]["power"]["powerState"]
    tap('.tile[data-key="SN-PLUG-1"]')
    time.sleep(1)
    after = mock_dev("SN-PLUG-1")["state"]["power"]["powerState"]
    check(f"tap toggles plug {before}->{after}", before != after)
    check("tile shows new state", js("document.querySelector('.tile[data-key=\"SN-PLUG-1\"]').classList.contains('on')") == (after == "on"))

    # channel tile tap
    b2 = mock_dev("SN-2GANG")["state"]["toggle"]["1"]["toggleState"]
    tap('.tile[data-key="SN-2GANG:1"]')
    time.sleep(1)
    a2 = mock_dev("SN-2GANG")["state"]["toggle"]["1"]["toggleState"]
    check(f"tap toggles channel 1 only {b2}->{a2}", b2 != a2)

    # curtain: tap opens fully
    tap('.tile[data-key="SN-CURTAIN"]')
    time.sleep(1)
    check("curtain tap -> 100% open", mock_dev("SN-CURTAIN")["state"]["percentage"]["percentage"] == 100)
    check("curtain tile says Open", "Open" in js("document.querySelector('.tile[data-key=\"SN-CURTAIN\"] .t-status').textContent"))

    # live update from outside the app (another client) reaches the tile
    req = urllib.request.Request(MOCK + "/open-api/v2/rest/devices/SN-BULB", method="PUT",
                                 data=json.dumps({"state": {"power": {"powerState": "on"}}}).encode(),
                                 headers={"Authorization": "Bearer test-token-123", "Content-Type": "application/json"})
    urllib.request.urlopen(req).read()
    time.sleep(1)
    check("external change pushed live", js("document.querySelector('.tile[data-key=\"SN-BULB\"]').classList.contains('on')"))

    # long-press opens detail sheet
    lp_before = mock_dev("SN-2GANG")["state"]["toggle"]["2"]["toggleState"]
    tap('.tile[data-key="SN-2GANG:2"]', hold=0.7)
    time.sleep(0.6)
    check("long-press opens sheet", js("!document.querySelector('#sheet').hidden && document.querySelector('#sheet').classList.contains('show')"))
    check("long-press did NOT toggle", mock_dev("SN-2GANG")["state"]["toggle"]["2"]["toggleState"] == lp_before)
    shot("2-phone-detail-sheet")

    room_before = mock_dev("SN-2GANG")["tags"].get("room")
    # rename channel 2 through the Name field
    js("""(() => { const i = document.querySelector('[data-rename="acc"]'); i.focus(); i.value = 'Reading Lamp';
            i.dispatchEvent(new Event('change', {bubbles:true})); i.blur(); })()""")
    time.sleep(1)
    tags = mock_dev("SN-2GANG")["tags"]
    check(f"channel rename saved to iHost tags ({tags.get('toggle')})", (tags.get("toggle") or {}).get("2") == "Reading Lamp")
    check("room tag preserved on rename", tags.get("room") == room_before)

    # rename the device
    js("""(() => { const i = document.querySelector('[data-rename="dev"]'); i.value = 'Living Wall 2-Gang';
            i.dispatchEvent(new Event('change', {bubbles:true})); })()""")
    time.sleep(1)
    check("device rename saved", mock_dev("SN-2GANG")["name"] == "Living Wall 2-Gang")

    # sibling switch inside the sheet
    sb = mock_dev("SN-2GANG")["state"]["toggle"]["1"]["toggleState"]
    js("document.querySelector('[data-sib=\"SN-2GANG:1\"]').click()")
    time.sleep(1)
    check("sibling switch in sheet works", mock_dev("SN-2GANG")["state"]["toggle"]["1"]["toggleState"] != sb)

    # move to a new room
    js("document.querySelector('[data-act=\"pick-room\"]').click()")
    time.sleep(0.4)
    shot("3-phone-room-picker")
    js("document.querySelector('#newRoom').value = 'Office'; document.querySelector('[data-act=\"add-room\"]').click()")
    time.sleep(1)
    check("room moved to Office", mock_dev("SN-2GANG")["tags"].get("room") == "Office")
    check("channel names kept after room change", (mock_dev("SN-2GANG")["tags"].get("toggle") or {}).get("2") == "Reading Lamp")

    # favourite + display-as
    js("document.querySelector('[data-icon=\"fan\"]').click()")
    time.sleep(0.5)
    js("document.querySelector('[data-act=\"fav\"]').click()")
    time.sleep(0.8)
    hub_id = json.load(urllib.request.urlopen(APP + "/api/hubs"))["hubs"][0]["id"]
    prefs = json.load(urllib.request.urlopen(APP + f"/api/hubs/{hub_id}/prefs"))
    check("favorite + icon saved", "SN-2GANG:2" in prefs["favorites"] and prefs["icons"].get("SN-2GANG:2") == "fan")
    shot("4-phone-sheet-after-edits")
    js("document.querySelector('[data-act=\"close\"]').click()")
    time.sleep(0.5)
    check("favorites section shown", js("[...document.querySelectorAll('.section h2')].map(h => h.textContent).includes('Favorites')"))
    check("Office room pill exists", js("[...document.querySelectorAll('.room-pill')].some(p => p.textContent === 'Office')"))
    shot("5-phone-home-after")

    # On Now chip -> turn off all
    js("document.querySelector('[data-chip=\"on\"]').click()")
    time.sleep(0.5)
    shot("6-phone-on-now")
    js("document.querySelector('[data-act=\"all-off\"]').click()")
    time.sleep(2)
    devs = [mock_dev(s) for s in ("SN-PLUG-1", "SN-2GANG", "SN-BULB")]
    all_off = devs[0]["state"]["power"]["powerState"] == "off" and devs[2]["state"]["power"]["powerState"] == "off" and \
        all(v["toggleState"] == "off" for v in devs[1]["state"]["toggle"].values())
    check("Turn Off All switched everything off", all_off)
    js("document.querySelector('[data-act=\"close\"]').click()")
    time.sleep(0.4)

    # room tab
    js("[...document.querySelectorAll('.room-pill')].find(p => p.textContent === 'Office').click()")
    time.sleep(0.4)
    check("room tab filters", js("document.querySelectorAll('.tile').length") == 2)
    shot("7-phone-room-tab")

    # settings
    js("document.querySelector('[data-room=\"__home\"]').click(); document.querySelector('#settingsBtn').click()")
    time.sleep(0.6)
    shot("8-phone-settings")
    js("document.querySelector('[data-act=\"close\"]').click()")
    time.sleep(0.6)

    # ---- desktop, light
    cdp("Emulation.setDeviceMetricsOverride", width=1280, height=820, deviceScaleFactor=1, mobile=False)
    cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
    time.sleep(0.4)
    bb = mock_dev("SN-BULB")["state"]["power"]["powerState"]
    tap('.tile[data-key="SN-BULB"]')
    time.sleep(0.8)
    check("desktop click toggles", mock_dev("SN-BULB")["state"]["power"]["powerState"] != bb)
    shot("9-desktop-light")
    x, y = center('.tile[data-key="SN-BULB"]')
    cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="right", clickCount=1)
    cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="right", clickCount=1)
    time.sleep(0.6)
    check("right-click opens details", js("document.querySelector('#sheet').classList.contains('show')"))
    shot("10-desktop-light-sheet")
    cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
    time.sleep(0.3)
    shot("11-desktop-dark-sheet")

    check(f"no JS errors ({errors})", not errors)
    print("FAILURES:", check.fails)
finally:
    proc.terminate()
