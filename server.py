#!/usr/bin/env python3
"""iHost Home - web app server for SONOFF iHost hubs (eWeLink CUBE Open API V2).

- Serves the web UI from ./static
- Connects to one or more iHosts by their LAN IP (with a network scan to find them)
- Proxies the iHost REST API (tokens never leave this machine)
- Keeps ONE live event stream (SSE) per iHost and fans it out to every open browser
- Optional internet access through a Cloudflare Tunnel, protected by a password login

Python standard library only.  Run:  python server.py   then open http://<this-pc-ip>:8088
"""
import concurrent.futures
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import platform
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA = os.environ.get("IHOST_HOME_DATA") or HERE
CONFIG = os.path.join(DATA, "config.json")
PREFS = os.path.join(DATA, "prefs.json")
BIN = os.path.join(DATA, "bin")
API = "/open-api/v2/rest"
APP_NAME = "iHost Home"
HIDDEN_TAGS = ("deviceInfo_built_in",)  # big base64 blobs the UI never needs
SESSION_DAYS = 90

lock = threading.RLock()


# ------------------------------------------------------------------ storage
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def new_id():
    return secrets.token_hex(4)


def hash_secret(value, salt=None, rounds=200_000):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, rounds)
    return f"pbkdf2${rounds}${salt.hex()}${dk.hex()}"


def check_secret(value, stored):
    try:
        _, rounds, salt, dk = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(test.hex(), dk)
    except (ValueError, AttributeError):
        return False


def load_config():
    raw = _read(CONFIG, {})
    cfg = {
        "hubs": [], "port": 8088, "bind": "0.0.0.0", "secret": "",
        "auth": {"password": "", "link": "", "lan_login": False, "epoch": 1, "legacy_pin": False},
        "remote": {"enabled": False, "mode": "quick", "tunnel_token": "", "public_url": ""},
    }
    for k in ("hubs", "port", "bind", "secret"):
        if k in raw:
            cfg[k] = raw[k]
    cfg["auth"].update(raw.get("auth") or {})
    cfg["remote"].update(raw.get("remote") or {})
    # migrate v1 (single iHost + numeric PIN)
    if raw.get("host") and raw.get("token") and not cfg["hubs"]:
        old_name = (_read(PREFS, {}) or {}).get("home_name") or "My Home"  # v1 kept the home name in prefs
        cfg["hubs"].append({"id": new_id(), "name": old_name, "host": raw["host"], "token": raw["token"]})
    if raw.get("pin") and not cfg["auth"]["password"]:
        cfg["auth"]["password"] = hash_secret(raw["pin"])
        cfg["auth"]["lan_login"] = True
        cfg["auth"]["legacy_pin"] = True  # short numeric PIN: fine at home, not for the internet
    if not cfg["hubs"] and DATA == HERE:  # reuse the pairing from the ihost-control CLI if it sits next to us
        old = _read(os.path.join(os.path.dirname(HERE), "ihost-control", "config.json"), {})
        if old.get("token"):
            cfg["hubs"].append({"id": new_id(), "name": "My Home", "host": old.get("host", ""), "token": old["token"]})
    if not cfg["secret"]:
        cfg["secret"] = secrets.token_hex(32)
    _write(CONFIG, cfg)
    return cfg


CFG = load_config()


def save_config():
    with lock:
        _write(CONFIG, CFG)


def load_prefs():
    raw = _read(PREFS, {})
    if "hubs" not in raw:  # migrate v1 flat prefs onto the first hub
        raw = {"hubs": ({CFG["hubs"][0]["id"]: raw} if raw and CFG["hubs"] else {})}
    return raw


def hub_prefs(hub_id):
    p = {"favorites": [], "icons": {}}
    p.update(load_prefs()["hubs"].get(hub_id, {}))
    return p


def save_hub_prefs(hub_id, patch):
    with lock:
        allp = load_prefs()
        cur = allp["hubs"].get(hub_id, {})
        cur.update(patch)
        allp["hubs"][hub_id] = cur
        _write(PREFS, allp)
    return hub_prefs(hub_id)


def get_hub(hub_id):
    return next((h for h in CFG["hubs"] if h["id"] == hub_id), None)


def public_hub(h):
    return {"id": h["id"], "name": h.get("name") or "iHost", "host": h["host"], "deviceid": h.get("deviceid")}


# ------------------------------------------------------------------ iHost client
class GatewayError(Exception):
    pass


def norm_host(host):
    h = (host or "").strip().rstrip("/")
    return h if h.startswith("http") else "http://" + h


def ihost(hub, method, path, body=None, auth=True, timeout=8):
    req = urllib.request.Request(norm_host(hub["host"]) + API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", "Bearer " + hub.get("token", ""))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise GatewayError(f"iHost unreachable at {hub['host']} ({getattr(e, 'reason', e)})")
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise GatewayError("iHost sent an invalid reply")


def device_list(hub, strip=True):
    res = ihost(hub, "GET", "/devices")
    if res.get("error") != 0:
        raise GatewayError(f"iHost error {res.get('error')}: {res.get('message')}")
    devs = res["data"].get("device_list", [])
    if strip:
        for d in devs:
            d["tags"] = {k: v for k, v in (d.get("tags") or {}).items() if k not in HIDDEN_TAGS}
    return devs


def merge_tags(hub, sn, patch):
    """Merge a partial tags object into the device's FULL tags (keeps the iHost's internal keys).
    The iHost shallow-merges top-level tag keys on PUT and only clears a key sent as null
    (verified on fw 2.14.1), so removals are sent as null rather than omitted."""
    dev = next((d for d in device_list(hub, strip=False) if d["serial_number"] == sn), None)
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
    return ihost(hub, "PUT", "/devices/" + urllib.parse.quote(sn), {"tags": tags})


def probe_ihost(host, timeout=2.5):
    """GET /bridge (needs no token). Returns the bridge info dict, or None if it isn't an iHost."""
    try:
        with urllib.request.urlopen(norm_host(host) + API + "/bridge", timeout=timeout) as r:
            res = json.loads(r.read() or b"{}")
        if res.get("error") == 0 and isinstance(res.get("data"), dict) and (
                res["data"].get("deviceid") or res["data"].get("fw_version")):
            return res["data"]
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ live events (one stream per iHost)
class EventHub:
    def __init__(self, hub_id):
        self.hub_id = hub_id
        self.clients = []
        self.connected = False
        self.stopped = False
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
        while not self.stopped:
            hub = get_hub(self.hub_id)
            if not hub:
                return
            url = norm_host(hub["host"]) + "/open-api/v2/sse/bridge?access_token=" + urllib.parse.quote(hub["token"])
            try:
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=600) as r:
                    self.set_connected(True)
                    event, data = None, []
                    for raw in r:
                        if self.stopped:
                            return
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


EVENTS = {}


def events_for(hub_id):
    with lock:
        if hub_id not in EVENTS:
            EVENTS[hub_id] = EventHub(hub_id)
        return EVENTS[hub_id]


def restart_events(hub_id):
    with lock:
        ev = EVENTS.pop(hub_id, None)
    if ev:
        ev.stopped = True
    return events_for(hub_id)


for _h in CFG["hubs"]:
    events_for(_h["id"])


# ------------------------------------------------------------------ pairing + network scan
PAIRS = {}  # job id -> state


def pair_worker(job, host, name):
    st = PAIRS[job]
    info = probe_ihost(host)
    if not info:
        st.update(state="error", message=f"No iHost answered at {host}. Check the IP and that this computer is on the same network.")
        return
    st.update(state="waiting", bridge={k: info.get(k) for k in ("name", "ip", "fw_version", "mac")},
              message="Open the iHost web console and click Done")
    tmp = {"host": host, "token": ""}
    deadline = time.time() + 300
    while time.time() < deadline and st["state"] == "waiting":
        try:
            res = ihost(tmp, "GET", "/bridge/access_token?app_name=" + urllib.parse.quote(APP_NAME), auth=False)
        except GatewayError as e:
            st.update(state="error", message=str(e))
            return
        if res.get("error") == 0 and res.get("data", {}).get("token"):
            token = res["data"]["token"]
            with lock:
                did = info.get("deviceid")
                existing = next((h for h in CFG["hubs"] if (did and h.get("deviceid") == did)
                                 or norm_host(h["host"]) == norm_host(host)), None)
                if existing:  # same iHost again (new IP or new token): update it in place
                    existing.update(host=host, token=token, deviceid=did)
                    if name:
                        existing["name"] = name
                    hub = existing
                else:
                    hub = {"id": new_id(), "name": name or ("My Home" if not CFG["hubs"] else f"Home {len(CFG['hubs']) + 1}"),
                           "host": host, "token": token, "deviceid": did}
                    CFG["hubs"].append(hub)
                _write(CONFIG, CFG)
            restart_events(hub["id"])
            st.update(state="done", message="Paired", hub=public_hub(hub))
            return
        time.sleep(2.5)
    if st["state"] == "waiting":
        st.update(state="timeout", message="Timed out - Done was not clicked within 5 minutes")


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    out = []
    for ip in ips:
        try:
            a = ipaddress.ip_address(ip)
            if a.is_private and not a.is_loopback and not a.is_link_local:
                out.append(ip)
        except ValueError:
            pass
    return sorted(out)


def scan_network():
    """Find iHosts: ihost.local, then every address of each local /24 network."""
    candidates = ["ihost.local"]
    for ip in lan_ips():
        candidates += [str(h) for h in ipaddress.ip_network(ip + "/24", strict=False).hosts()]
    found, seen = [], set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=96) as ex:
        futs = {ex.submit(probe_ihost, c, 1.2): c for c in candidates}
        for f in concurrent.futures.as_completed(futs):
            info = f.result()
            if not info:
                continue
            key = info.get("deviceid") or futs[f]
            if key in seen:
                continue
            seen.add(key)
            ip = info.get("ip") or futs[f]
            paired = next((h["name"] for h in CFG["hubs"] if (info.get("deviceid") and h.get("deviceid") == info.get("deviceid"))
                           or norm_host(h["host"]) == norm_host(ip)), None)
            found.append({"ip": ip, "name": info.get("name") or "iHost", "fw": info.get("fw_version"),
                          "mac": info.get("mac"), "deviceid": info.get("deviceid"), "paired_as": paired})
    return sorted(found, key=lambda x: tuple(int(p) if p.isdigit() else 0 for p in x["ip"].split(".")))


# ------------------------------------------------------------------ internet access (Cloudflare Tunnel)
class Tunnel:
    def __init__(self):
        self.proc = None
        self.state = "off"     # off | downloading | starting | verifying | running | error
        self.url = ""
        self.error = ""
        self.gen = 0
        self.t_lock = threading.Lock()

    @staticmethod
    def exe_name():
        return "cloudflared.exe" if os.name == "nt" else "cloudflared"

    def find(self):
        local = os.path.join(BIN, self.exe_name())
        return local if os.path.isfile(local) else shutil.which("cloudflared")

    def download(self):
        system, machine = platform.system().lower(), platform.machine().lower()
        arch = "arm64" if machine in ("arm64", "aarch64") else "arm" if machine.startswith("arm") else "amd64"
        if system == "windows":
            asset = f"cloudflared-windows-{'amd64' if arch == 'amd64' else '386'}.exe"
        elif system == "linux":
            asset = f"cloudflared-linux-{arch}"
        else:
            raise RuntimeError("Install cloudflared first (macOS: brew install cloudflared)")
        os.makedirs(BIN, exist_ok=True)
        dest = os.path.join(BIN, self.exe_name())
        tmp = dest + ".part"
        url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{asset}"
        with urllib.request.urlopen(url, timeout=180) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(tmp, dest)
        if os.name != "nt":
            os.chmod(dest, 0o755)
        return dest

    def start(self):
        with self.t_lock:
            if self.proc and self.proc.poll() is None:
                return
            self.gen += 1
            gen = self.gen
            self.error, self.url, self.state = "", "", "starting"
        threading.Thread(target=self._run, args=(gen,), daemon=True).start()

    def _run(self, gen):
        try:
            exe = self.find()
            if not exe:
                self.state = "downloading"
                exe = self.download()
            if gen != self.gen:
                return
            self.state = "starting"
            r = CFG["remote"]
            port = int(os.environ.get("PORT") or CFG["port"])
            if r["mode"] == "token" and r.get("tunnel_token"):
                cmd = [exe, "tunnel", "--no-autoupdate", "run", "--token", r["tunnel_token"]]
                self.url = (r.get("public_url") or "").rstrip("/")
            else:
                cmd = [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    creationflags=flags, text=True, encoding="utf-8", errors="replace")
            with self.t_lock:
                self.proc = proc
            for line in proc.stdout:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and r["mode"] != "token":
                    self.url = m.group(0)
                if "Registered tunnel connection" in line and self.state not in ("verifying", "running"):
                    self.state = "verifying"
                    threading.Thread(target=self._verify, args=(gen,), daemon=True).start()
                elif " ERR " in line and self.state not in ("verifying", "running"):
                    self.error = line.strip()[-240:]
            code = proc.wait()
            if gen == self.gen and CFG["remote"]["enabled"]:
                self.state, self.error = "error", (self.error or f"cloudflared stopped (exit {code})") + " - retrying"
                time.sleep(10)
                if gen == self.gen and CFG["remote"]["enabled"]:
                    with self.t_lock:
                        self.proc = None
                    self.start()
        except Exception as e:
            self.state, self.error = "error", str(e)

    def _verify(self, gen):
        """Only report 'running' once the public address really works. A brand-new trycloudflare name
        needs a few seconds to appear in DNS; opening it too early makes phones cache the failure for
        minutes. So ask Cloudflare's DNS over HTTPS (keeps this PC's DNS cache clean), then fetch it."""
        host = urllib.parse.urlparse(self.url).hostname if self.url else None
        deadline = time.time() + 180
        resolved = False
        while gen == self.gen and time.time() < deadline:
            if not host:
                self.state = "running"  # own domain without a configured address: nothing to probe
                return
            try:
                if not resolved:
                    req = urllib.request.Request(f"https://cloudflare-dns.com/dns-query?name={host}&type=A",
                                                 headers={"Accept": "application/dns-json"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        ans = json.loads(resp.read())
                    resolved = ans.get("Status") == 0 and bool(ans.get("Answer"))
                if resolved:
                    with urllib.request.urlopen(self.url + "/api/session", timeout=10) as resp:
                        if resp.status == 200:
                            self.state = "running"
                            return
            except Exception:
                pass
            time.sleep(3)
        if gen == self.gen and self.state == "verifying":
            self.state = "running"  # couldn't confirm from here (e.g. DNS-over-HTTPS blocked); show it anyway
            self.error = "Couldn't confirm the link from this computer yet - if it doesn't open, wait a minute and retry."

    def stop(self):
        with self.t_lock:
            self.gen += 1
            p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.state, self.url, self.error = "off", "", ""

    def status(self):
        r = CFG["remote"]
        return {"enabled": r["enabled"], "mode": r["mode"], "state": self.state,
                "url": self.url if self.state == "running" else "",
                "error": self.error, "has_token": bool(r.get("tunnel_token")), "public_url": r.get("public_url", "")}


TUNNEL = Tunnel()


# ------------------------------------------------------------------ auth
FAILS_IP = {}
FAILS_ALL = []


def session_cookie():
    body = f"{CFG['auth']['epoch']}.{secrets.token_hex(12)}"
    sig = hmac.new(CFG["secret"].encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def valid_session(value):
    try:
        epoch, nonce, sig = value.split(".")
    except ValueError:
        return False
    good = hmac.new(CFG["secret"].encode(), f"{epoch}.{nonce}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and epoch == str(CFG["auth"]["epoch"])


def throttled(ip):
    now = time.time()
    FAILS_ALL[:] = [t for t in FAILS_ALL if now - t < 3600]
    FAILS_IP[ip] = [t for t in FAILS_IP.get(ip, []) if now - t < 900]
    return len(FAILS_IP[ip]) >= 5 or len(FAILS_ALL) >= 30


def record_fail(ip):
    FAILS_IP.setdefault(ip, []).append(time.time())
    FAILS_ALL.append(time.time())


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "iHostHome/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # -- request facts
    def is_remote(self):
        """True for anything that didn't come straight from the local network: Cloudflare-tunnelled
        requests (the edge always adds CF-* headers, which clients can't strip), proxied requests,
        and any public source address (e.g. router port-forwarding)."""
        if self.headers.get("CF-Connecting-IP") or self.headers.get("Cf-Ray") or self.headers.get("X-Forwarded-For"):
            return True
        try:
            ip = ipaddress.ip_address(self.client_address[0])
            return not (ip.is_private or ip.is_loopback or ip.is_link_local)
        except ValueError:
            return True

    def client_ip(self):
        return (self.headers.get("CF-Connecting-IP") or self.client_address[0]).strip()

    def is_https(self):
        return self.headers.get("X-Forwarded-Proto") == "https" or '"https"' in (self.headers.get("CF-Visitor") or "")

    def login_required(self):
        return self.is_remote() or (CFG["auth"]["lan_login"] and bool(CFG["auth"]["password"]))

    def authed(self):
        if not self.login_required():
            return True
        if self.is_remote() and not CFG["remote"]["enabled"]:
            return False  # internet access switched off: nothing from outside gets in
        if not (CFG["auth"]["password"] or CFG["auth"]["link"]):
            return False
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "ihh" and valid_session(v):
                return True
        return False

    def cookie_header(self, value, max_age):
        secure = "; Secure" if self.is_https() else ""
        return f"ihh={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def new_session_header(self):
        return {"Set-Cookie": self.cookie_header(session_cookie(), SESSION_DAYS * 86400)}

    # -- helpers
    def common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def send_json(self, obj, code=200, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.common_headers()
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location, extra_headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.common_headers()
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def read_raw_body(self):
        """Always drain the request body first: replying early (401/403/429) without reading it
        would leave bytes on a kept-alive connection (Cloudflare reuses them) and corrupt the next request."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > 1_000_000:
            self.close_connection = True
            self._raw = None
            return
        self._raw = self.rfile.read(n) if n > 0 else b""

    def body(self):
        raw = getattr(self, "_raw", b"")
        if raw is None:
            raise GatewayError("request too large")
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise GatewayError("invalid JSON")

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

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        self.read_raw_body()
        url = urllib.parse.urlparse(self.path)
        path = url.path
        try:
            if path == "/login" and method == "GET":
                return self.link_login(urllib.parse.parse_qs(url.query).get("key", [""])[0])
            if not path.startswith("/api/"):
                return self.static(path) if method == "GET" else self.send_json({"error": "not found"}, 404)
            if method != "GET" and not self.same_origin():
                return self.send_json({"error": "cross-origin request blocked"}, 403)
            if path == "/api/login" and method == "POST":
                return self.login()
            if path == "/api/logout" and method == "POST":
                return self.send_json({"ok": True}, extra_headers={"Set-Cookie": self.cookie_header("", 0)})
            if path == "/api/session":
                return self.send_json({"authed": self.authed(), "login_required": self.login_required(),
                                       "remote": self.is_remote(), "has_password": bool(CFG["auth"]["password"]),
                                       "remote_enabled": CFG["remote"]["enabled"]})
            if not self.authed():
                return self.send_json({"error": "login required"}, 401)
            return self.api(method, path)
        except GatewayError as e:
            self.send_json({"error": str(e)}, 502)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def api(self, method, path):
        parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/")]  # ['api', ...]

        # ---------- homes (iHosts)
        if path == "/api/hubs" and method == "GET":
            return self.send_json({"hubs": [dict(public_hub(h), live=events_for(h["id"]).connected) for h in CFG["hubs"]]})
        if path == "/api/scan" and method == "POST":
            if self.is_remote():
                return self.send_json({"error": "Scanning only works from the home network"}, 403)
            return self.send_json({"found": scan_network(), "networks": lan_ips()})
        if path == "/api/pair" and method == "POST":
            if self.is_remote():
                return self.send_json({"error": "Add iHosts from the home network"}, 403)
            b = self.body()
            host = str(b.get("host", "")).strip()
            if not host:
                return self.send_json({"error": "address required"}, 400)
            job = new_id()
            PAIRS[job] = {"state": "probing", "message": "Looking for the iHost..."}
            threading.Thread(target=pair_worker, args=(job, host, str(b.get("name", "")).strip()[:40]), daemon=True).start()
            return self.send_json({"job": job})
        if len(parts) == 3 and parts[1] == "pair":
            if method == "GET":
                st = PAIRS.get(parts[2])
                return self.send_json(st or {"state": "error", "message": "unknown pairing job"}, 200 if st else 404)
            if method == "DELETE":
                if parts[2] in PAIRS:
                    PAIRS[parts[2]]["state"] = "cancelled"
                return self.send_json({"ok": True})

        if len(parts) >= 3 and parts[1] == "hubs":
            hub = get_hub(parts[2])
            if not hub:
                return self.send_json({"error": "unknown iHost"}, 404)
            return self.hub_api(method, hub, parts[3:])

        # ---------- security + internet access
        if path == "/api/security" and method == "GET":
            return self.send_json({"has_password": bool(CFG["auth"]["password"]), "lan_login": CFG["auth"]["lan_login"],
                                   "weak_password": bool(CFG["auth"].get("legacy_pin")),
                                   "has_link": bool(CFG["auth"]["link"]), "remote": TUNNEL.status(),
                                   "this_request_remote": self.is_remote()})
        if path == "/api/security/password" and method == "POST":
            b = self.body()
            new = str(b.get("new", ""))
            if CFG["auth"]["password"] and not check_secret(str(b.get("current", "")), CFG["auth"]["password"]):
                record_fail(self.client_ip())
                return self.send_json({"error": "Current password is wrong"}, 403)
            if len(new) < 4:
                return self.send_json({"error": "Use at least 4 characters"}, 400)
            if CFG["remote"]["enabled"] and len(new) < 8:
                return self.send_json({"error": "Internet access is on, so use at least 8 characters"}, 400)
            CFG["auth"]["password"] = hash_secret(new)
            CFG["auth"]["legacy_pin"] = len(new) < 8  # short PINs are fine at home, too weak for the internet
            CFG["auth"]["epoch"] += 1  # sign out every other device
            save_config()
            return self.send_json({"ok": True}, extra_headers=self.new_session_header())
        if path == "/api/security/settings" and method == "PUT":
            b = self.body()
            if "lan_login" in b:
                if b["lan_login"] and not CFG["auth"]["password"]:
                    return self.send_json({"error": "Set a password first"}, 400)
                CFG["auth"]["lan_login"] = bool(b["lan_login"])
            save_config()
            return self.send_json({"ok": True})
        if path == "/api/security/link" and method == "POST":
            key = secrets.token_urlsafe(24)
            CFG["auth"]["link"] = hash_secret(key, rounds=50_000)
            save_config()
            return self.send_json({"key": key})
        if path == "/api/security/link" and method == "DELETE":
            CFG["auth"]["link"] = ""
            save_config()
            return self.send_json({"ok": True})
        if path == "/api/security/signout-all" and method == "POST":
            CFG["auth"]["epoch"] += 1
            save_config()
            return self.send_json({"ok": True}, extra_headers=self.new_session_header())
        if path == "/api/remote" and method == "PUT":
            b = self.body()
            r = CFG["remote"]
            if b.get("enabled") and not CFG["auth"]["password"]:
                return self.send_json({"error": "Set a password before turning on internet access"}, 400)
            if b.get("enabled") and CFG["auth"].get("legacy_pin"):
                return self.send_json({"error": "Internet access needs a password of at least 8 characters. Change it under Password first."}, 400)
            if self.is_remote() and ("enabled" in b or "mode" in b or "tunnel_token" in b):
                return self.send_json({"error": "Change internet access from the home network"}, 403)
            if b.get("mode") in ("quick", "token"):
                r["mode"] = b["mode"]
            if "tunnel_token" in b:
                r["tunnel_token"] = str(b["tunnel_token"]).strip()
            if "public_url" in b:
                pu = str(b["public_url"]).strip().rstrip("/")
                if pu and not pu.startswith("https://"):
                    pu = "https://" + pu
                r["public_url"] = pu
            if r["mode"] == "token" and b.get("enabled") and not r["tunnel_token"]:
                return self.send_json({"error": "Paste the Cloudflare tunnel token first"}, 400)
            restart = "enabled" in b or "mode" in b or "tunnel_token" in b
            if "enabled" in b:
                r["enabled"] = bool(b["enabled"])
            save_config()
            if restart:
                TUNNEL.stop()
                if r["enabled"]:
                    TUNNEL.start()
            elif "public_url" in b and r["mode"] == "token":
                TUNNEL.url = r["public_url"]
            return self.send_json(TUNNEL.status())
        return self.send_json({"error": "not found"}, 404)

    def hub_api(self, method, hub, rest):
        if not rest and method == "PATCH":
            b = self.body()
            if str(b.get("name", "")).strip():
                hub["name"] = str(b["name"]).strip()[:40]
            save_config()
            return self.send_json(public_hub(hub))
        if not rest and method == "DELETE":
            if self.is_remote():
                return self.send_json({"error": "Remove iHosts from the home network"}, 403)
            with lock:
                CFG["hubs"] = [h for h in CFG["hubs"] if h["id"] != hub["id"]]
                _write(CONFIG, CFG)
                ev = EVENTS.pop(hub["id"], None)
            if ev:
                ev.stopped = True
            return self.send_json({"ok": True})
        if rest == ["status"] and method == "GET":
            info, err = None, None
            try:
                info = ihost(hub, "GET", "/bridge", timeout=4).get("data")
            except GatewayError as e:
                err = str(e)
            return self.send_json({"hub": public_hub(hub), "bridge": info, "error": err, "live": events_for(hub["id"]).connected})
        if rest == ["devices"] and method == "GET":
            return self.send_json({"devices": device_list(hub)})
        if len(rest) == 2 and rest[0] == "devices" and method == "PUT":
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
            res = ihost(hub, "PUT", "/devices/" + urllib.parse.quote(rest[1]), payload)
            return self.send_json(res, 200 if res.get("error") == 0 else 502)
        if len(rest) == 3 and rest[0] == "devices" and rest[2] == "tags" and method == "PATCH":
            res = merge_tags(hub, rest[1], self.body())
            return self.send_json(res, 200 if res.get("error") == 0 else 502)
        if rest == ["prefs"]:
            if method == "GET":
                return self.send_json(hub_prefs(hub["id"]))
            if method == "PUT":
                b = self.body()
                return self.send_json(save_hub_prefs(hub["id"], {k: b[k] for k in ("favorites", "icons") if k in b}))
        if rest == ["events"] and method == "GET":
            return self.events(events_for(hub["id"]))
        return self.send_json({"error": "not found"}, 404)

    def login(self):
        ip = self.client_ip()
        if self.is_remote() and not CFG["remote"]["enabled"]:
            return self.send_json({"error": "Internet access is turned off for this home"}, 403)
        if throttled(ip):
            return self.send_json({"error": "Too many attempts - try again later"}, 429)
        pw = str(self.body().get("password", ""))
        if CFG["auth"]["password"] and check_secret(pw, CFG["auth"]["password"]):
            FAILS_IP.pop(ip, None)
            return self.send_json({"ok": True}, extra_headers=self.new_session_header())
        record_fail(ip)
        time.sleep(0.5)
        return self.send_json({"error": "Wrong password"}, 403)

    def link_login(self, key):
        ip = self.client_ip()
        if self.is_remote() and not CFG["remote"]["enabled"]:
            return self.redirect("/")
        if not throttled(ip) and key and CFG["auth"]["link"] and check_secret(key, CFG["auth"]["link"]):
            return self.redirect("/", self.new_session_header())
        record_fail(ip)
        return self.redirect("/?link=invalid")

    def events(self, ev):
        if (self.headers.get("Host") or "").split(":")[0].endswith(".trycloudflare.com"):
            # Cloudflare quick tunnels buffer event streams (documented limitation) and cap in-flight
            # requests, so refuse at once; the app then refreshes by polling instead.
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # Proper chunked encoding: proxies such as cloudflared only forward an open-ended stream
        # promptly when every event arrives as its own HTTP chunk.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def chunk(text):
            data = text.encode()
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        q = ev.subscribe()
        try:
            chunk(": " + " " * 2048 + "\n\n")  # padding: makes some proxies/browsers start delivering at once
            chunk(f"retry: 3000\nevent: gateway\ndata: {json.dumps({'connected': ev.connected})}\n\n")
            while not ev.stopped:
                try:
                    event, data = q.get(timeout=15)
                    chunk(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n")
                except queue.Empty:
                    chunk(": ping\n\n")
        except Exception:
            pass
        finally:
            ev.unsubscribe(q)
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
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.common_headers()
        self.end_headers()
        self.wfile.write(data)


def main():
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    mimetypes.add_type("application/javascript", ".js")
    port = int(os.environ.get("PORT") or CFG["port"])

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
                return  # browser closed a connection - normal, not worth a traceback
            super().handle_error(request, client_address)

    srv = Server((CFG["bind"], port), Handler)
    print(f"iHost Home running:  http://localhost:{port}")
    for ip in lan_ips():
        print(f"  on your network:    http://{ip}:{port}")
    for h in CFG["hubs"]:
        print(f"  iHost '{h['name']}': {h['host']}")
    if not CFG["hubs"]:
        print("  no iHost yet - open the page to add one")
    if CFG["remote"]["enabled"]:
        print("  internet access: starting Cloudflare Tunnel (link in the app: ••• > Internet Access)")
        TUNNEL.start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        TUNNEL.stop()


if __name__ == "__main__":
    main()
