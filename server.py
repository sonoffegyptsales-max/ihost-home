#!/usr/bin/env python3
"""iHost Home - local web app server for a SONOFF iHost (eWeLink CUBE Open API V2).

- Serves the web UI from ./static
- Proxies the iHost REST API (the token never leaves this machine)
- Keeps ONE live event stream (SSE) to the iHost and fans it out to every open browser
- Optional PIN login for other devices on the network

Python standard library only.  Run:  python server.py   then open http://<this-pc-ip>:8088
"""
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
CONFIG = os.path.join(HERE, "config.json")
PREFS = os.path.join(HERE, "prefs.json")
API = "/open-api/v2/rest"
APP_NAME = "iHost Home"
HIDDEN_TAGS = ("deviceInfo_built_in",)  # big base64 blobs the UI never needs

lock = threading.Lock()


# ------------------------------------------------------------------ storage
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_config():
    cfg = {"host": "", "token": "", "port": 8088, "bind": "0.0.0.0", "pin": "", "secret": ""}
    cfg.update(_read(CONFIG, {}))
    if not cfg["host"] and not cfg["token"]:
        # first run: reuse the pairing from the ihost-control CLI if it exists
        old = _read(os.path.join(os.path.dirname(HERE), "ihost-control", "config.json"), {})
        if old.get("token"):
            cfg["host"], cfg["token"] = old.get("host", ""), old["token"]
    if not cfg["secret"]:
        cfg["secret"] = secrets.token_hex(16)
    _write(CONFIG, cfg)
    return cfg


CFG = load_config()


def save_config():
    with lock:
        _write(CONFIG, CFG)


def load_prefs():
    p = {"favorites": [], "icons": {}, "home_name": "My Home"}
    p.update(_read(PREFS, {}))
    return p


# ------------------------------------------------------------------ iHost client
class GatewayError(Exception):
    pass


def base():
    h = CFG["host"].strip().rstrip("/")
    return h if h.startswith("http") else "http://" + h


def ihost(method, path, body=None, auth=True, timeout=8):
    if not CFG["host"]:
        raise GatewayError("not paired")
    req = urllib.request.Request(base() + API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", "Bearer " + CFG["token"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise GatewayError(f"iHost unreachable at {base()} ({getattr(e, 'reason', e)})")
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise GatewayError("iHost sent an invalid reply")


def device_list(strip=True):
    res = ihost("GET", "/devices")
    if res.get("error") != 0:
        raise GatewayError(f"iHost error {res.get('error')}: {res.get('message')}")
    devs = res["data"].get("device_list", [])
    if strip:
        for d in devs:
            d["tags"] = {k: v for k, v in (d.get("tags") or {}).items() if k not in HIDDEN_TAGS}
    return devs


def merge_tags(sn, patch):
    """Merge a partial tags object into the device's FULL tags (keeps the iHost's internal keys).
    null removes a key; the 'toggle' (channel names) object is merged one level deep.
    The iHost itself shallow-merges top-level tag keys on PUT and only clears a key that is sent
    as null (verified on fw 2.14.1), so removals are sent as null rather than omitted."""
    devs = device_list(strip=False)
    dev = next((d for d in devs if d["serial_number"] == sn), None)
    if not dev:
        raise GatewayError("device not found")
    tags = dict(dev.get("tags") or {})
    for k, v in patch.items():
        if v is None:
            tags[k] = None
        elif k == "toggle" and isinstance(v, dict):
            names = dict(tags.get("toggle") or {})
            for ch, name in v.items():
                if name in (None, ""):
                    names.pop(str(ch), None)
                else:
                    names[str(ch)] = str(name)[:64]
            tags["toggle"] = names or None
        else:
            tags[k] = v
    return ihost("PUT", "/devices/" + urllib.parse.quote(sn), {"tags": tags})


# ------------------------------------------------------------------ live events hub
class Hub:
    """One upstream SSE connection to the iHost, fanned out to all browser clients."""

    def __init__(self):
        self.clients = []
        self.connected = False
        self.cl_lock = threading.Lock()
        threading.Thread(target=self.run, daemon=True).start()

    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self.cl_lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.cl_lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, event, data):
        with self.cl_lock:
            for q in list(self.clients):
                try:
                    q.put_nowait((event, data))
                except queue.Full:
                    self.clients.remove(q)

    def set_connected(self, value):
        if value != self.connected:
            self.connected = value
            self.publish("gateway", {"connected": value})

    def run(self):
        while True:
            if not (CFG["host"] and CFG["token"]):
                time.sleep(2)
                continue
            url = base() + "/open-api/v2/sse/bridge?access_token=" + urllib.parse.quote(CFG["token"])
            try:
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=600) as r:
                    self.set_connected(True)
                    event, data = None, []
                    for raw in r:
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif line == "" and (event or data):
                            try:
                                payload = json.loads("\n".join(data))
                            except ValueError:
                                payload = {"raw": "\n".join(data)}
                            self.publish("ihost", {"type": event, "data": payload})
                            event, data = None, []
            except Exception:
                pass
            self.set_connected(False)
            time.sleep(3)


HUB = Hub()


# ------------------------------------------------------------------ pairing
PAIR = {"state": "idle", "message": ""}


def pair_worker(host):
    PAIR.update(state="waiting", message="Open the iHost web console and click Done")
    old = CFG["host"]
    CFG["host"] = host
    deadline = time.time() + 300
    while time.time() < deadline and PAIR["state"] == "waiting":
        try:
            res = ihost("GET", "/bridge/access_token?app_name=" + urllib.parse.quote(APP_NAME), auth=False)
        except GatewayError as e:
            CFG["host"] = old
            PAIR.update(state="error", message=str(e))
            return
        if res.get("error") == 0 and res.get("data", {}).get("token"):
            CFG["token"] = res["data"]["token"]
            save_config()
            PAIR.update(state="done", message="Paired")
            return
        time.sleep(2.5)
    if PAIR["state"] == "waiting":
        CFG["host"] = old
        PAIR.update(state="timeout", message="Timed out - Done was not clicked within 5 minutes")


# ------------------------------------------------------------------ auth (optional PIN)
def session_value():
    return hmac.new(CFG["secret"].encode(), ("pin:" + CFG["pin"]).encode(), hashlib.sha256).hexdigest()


LOGIN_FAILS = {}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "iHostHome/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # -- helpers
    def send_json(self, obj, code=200, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise GatewayError("request too large")
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            raise GatewayError("invalid JSON")

    def authed(self):
        if not CFG["pin"]:
            return True
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "ihh" and hmac.compare_digest(v, session_value()):
                return True
        return False

    def same_origin(self):
        """Block cross-site requests (CSRF) for state-changing calls."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urllib.parse.urlparse(origin).netloc == self.headers.get("Host")

    # -- routing
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_PATCH(self):
        self.route("PATCH")

    def route(self, method):
        path = urllib.parse.urlparse(self.path).path
        try:
            if not path.startswith("/api/"):
                return self.static(path) if method == "GET" else self.send_json({"error": "not found"}, 404)
            if method != "GET" and not self.same_origin():
                return self.send_json({"error": "cross-origin request blocked"}, 403)
            if path == "/api/login" and method == "POST":
                return self.login()
            if path == "/api/session":
                return self.send_json({"pin_required": bool(CFG["pin"]), "authed": self.authed()})
            if not self.authed():
                return self.send_json({"error": "login required"}, 401)
            return self.api(method, path)
        except GatewayError as e:
            self.send_json({"error": str(e)}, 502)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def api(self, method, path):
        parts = path.strip("/").split("/")  # ['api', ...]
        if path == "/api/status" and method == "GET":
            info, err = None, None
            if CFG["host"] and CFG["token"]:
                try:
                    r = ihost("GET", "/bridge", timeout=4)
                    info = r.get("data")
                except GatewayError as e:
                    err = str(e)
            return self.send_json({"paired": bool(CFG["host"] and CFG["token"]), "host": CFG["host"],
                                   "bridge": info, "error": err, "live": HUB.connected,
                                   "pin_enabled": bool(CFG["pin"])})
        if path == "/api/devices" and method == "GET":
            return self.send_json({"devices": device_list()})
        if len(parts) == 3 and parts[1] == "devices" and method == "PUT":
            sn = urllib.parse.unquote(parts[2])
            b = self.body()
            payload = {}
            if "state" in b:
                payload["state"] = b["state"]
            if "name" in b:
                name = str(b["name"]).strip()
                if not name or len(name) > 64:
                    return self.send_json({"error": "name must be 1-64 characters"}, 400)
                payload["name"] = name
            if not payload:
                return self.send_json({"error": "nothing to update"}, 400)
            res = ihost("PUT", "/devices/" + urllib.parse.quote(sn), payload)
            return self.send_json(res, 200 if res.get("error") == 0 else 502)
        if len(parts) == 4 and parts[1] == "devices" and parts[3] == "tags" and method == "PATCH":
            res = merge_tags(urllib.parse.unquote(parts[2]), self.body())
            return self.send_json(res, 200 if res.get("error") == 0 else 502)
        if path == "/api/prefs":
            if method == "GET":
                return self.send_json(load_prefs())
            if method == "PUT":
                p = load_prefs()
                b = self.body()
                for k in ("favorites", "icons", "home_name"):
                    if k in b:
                        p[k] = b[k]
                with lock:
                    _write(PREFS, p)
                return self.send_json(p)
        if path == "/api/pair":
            if method == "GET":
                return self.send_json(PAIR)
            if method == "POST":
                host = str(self.body().get("host", "")).strip()
                if not host:
                    return self.send_json({"error": "host required"}, 400)
                if PAIR["state"] == "waiting":
                    PAIR["state"] = "cancelled"
                    time.sleep(3)
                threading.Thread(target=pair_worker, args=(host,), daemon=True).start()
                return self.send_json({"state": "waiting"})
        if path == "/api/pin" and method == "POST":
            pin = str(self.body().get("pin", "")).strip()
            if pin and (not pin.isdigit() or not 4 <= len(pin) <= 8):
                return self.send_json({"error": "PIN must be 4-8 digits"}, 400)
            CFG["pin"] = pin
            save_config()
            headers = {"Set-Cookie": f"ihh={session_value()}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"} if pin else {}
            return self.send_json({"pin_enabled": bool(pin)}, extra_headers=headers)
        if path == "/api/events" and method == "GET":
            return self.events()
        return self.send_json({"error": "not found"}, 404)

    def login(self):
        ip = self.client_address[0]
        fails = [t for t in LOGIN_FAILS.get(ip, []) if time.time() - t < 300]
        if len(fails) >= 5:
            return self.send_json({"error": "too many attempts, wait 5 minutes"}, 429)
        pin = str(self.body().get("pin", ""))
        if CFG["pin"] and hmac.compare_digest(pin, CFG["pin"]):
            LOGIN_FAILS.pop(ip, None)
            return self.send_json({"ok": True}, extra_headers={
                "Set-Cookie": f"ihh={session_value()}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"})
        LOGIN_FAILS[ip] = fails + [time.time()]
        return self.send_json({"error": "wrong PIN"}, 403)

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = HUB.subscribe()
        try:
            self.wfile.write(f"event: gateway\ndata: {json.dumps({'connected': HUB.connected})}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    event, data = q.get(timeout=15)
                    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    msg = ": ping\n\n"
                self.wfile.write(msg.encode())
                self.wfile.flush()
        except Exception:
            pass
        finally:
            HUB.unsubscribe(q)
            self.close_connection = True

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            full = os.path.join(STATIC, "index.html")  # SPA fallback
        with open(full, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def main():
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    mimetypes.add_type("application/javascript", ".js")
    port = int(os.environ.get("PORT") or CFG["port"])
    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
                return  # browser closed a connection - normal, not worth a traceback
            super().handle_error(request, client_address)

    srv = Server((CFG["bind"], port), Handler)
    srv.daemon_threads = True
    print(f"iHost Home running:  http://localhost:{port}")
    for ip in lan_ips():
        print(f"  on your phone:      http://{ip}:{port}")
    print("  iHost:", CFG["host"] or "(not paired yet - open the page to pair)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
