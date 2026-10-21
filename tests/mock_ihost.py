#!/usr/bin/env python3
"""Fake iHost (Open API V2 subset) for testing ihost.py without a real gateway.

  python tests/mock_ihost.py 8765          then   set IHOST_HOST=http://127.0.0.1:8765
Pairing: the first access_token call returns 401 "link button not pressed", the next succeeds.
"""
import copy
import json
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

TOKEN = "test-token-123"
DEVICES = [
    {"serial_number": "SN-PLUG-1", "name": "Kitchen Plug", "display_category": "plug", "online": True,
     "capabilities": [{"capability": "power", "permission": "1100"}],
     "state": {"power": {"powerState": "off"}}, "tags": {"room": "kitchen"}},
    {"serial_number": "SN-2GANG", "name": "Living Wall Switch", "display_category": "switch", "online": True,
     "capabilities": [{"capability": "power", "permission": "1100"},
                      {"capability": "toggle", "permission": "1100", "name": "1"},
                      {"capability": "toggle", "permission": "1100", "name": "2"}],
     "state": {"power": {"powerState": "on"}, "toggle": {"1": {"toggleState": "on"}, "2": {"toggleState": "off"}}},
     "tags": {"room": "living"}},
    {"serial_number": "SN-BULB", "name": "Living Bulb", "display_category": "light", "online": True,
     "capabilities": [{"capability": "power", "permission": "1100"},
                      {"capability": "brightness", "permission": "1100"},
                      {"capability": "color-temperature", "permission": "1100"}],
     "state": {"power": {"powerState": "on"}, "brightness": {"brightness": 100},
               "color-temperature": {"colorTemperature": 50}}, "tags": {"room": "living"}},
    {"serial_number": "SN-CURTAIN", "name": "Bedroom Curtain", "display_category": "curtain", "online": True,
     "capabilities": [{"capability": "percentage", "permission": "1100"}],
     "state": {"percentage": {"percentage": 0}}, "tags": {"room": "bedroom"}},
    {"serial_number": "SN-TH", "name": "Bedroom Sensor", "display_category": "temperatureAndHumiditySensor",
     "online": True, "capabilities": [{"capability": "temperature", "permission": "0100"},
                                      {"capability": "humidity", "permission": "0100"}],
     "state": {"temperature": {"temperature": 24.5}, "humidity": {"humidity": 55}}, "tags": {}},
    {"serial_number": "SN-OFFLINE", "name": "Garage Plug", "display_category": "plug", "online": False,
     "capabilities": [{"capability": "power", "permission": "1100"}],
     "state": {"power": {"powerState": "off"}}, "tags": {}},
]
LOCK = threading.Lock()
PAIR = {"pressed": False}
LISTENERS = []
LOG = []  # every PUT body, for assertions


def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def flip(v):
    return {"on": "off", "off": "on"}.get(v, v)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authed(self):
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            self.send({"error": 401, "data": {}, "message": "invalid access_token"})
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/open-api/v2/rest/bridge/access_token":
            if not parse_qs(u.query).get("app_name"):  # real fw 2.14.1 behaviour
                return self.send({"error": 400, "data": {}, "message": "app_name is required!"})
            if not PAIR["pressed"]:
                PAIR["pressed"] = True
                return self.send({"error": 401, "data": {}, "message": "link button not pressed"})
            return self.send({"error": 0, "data": {"token": TOKEN}, "message": "success"})
        if u.path == "/open-api/v2/rest/bridge":
            port = self.server.server_address[1]
            return self.send({"error": 0, "data": {"ip": "127.0.0.1", "mac": "00:00", "fw_version": "mock",
                                                   "deviceid": f"mock-{port}", "name": f"Mock iHost {port}"},
                              "message": "success"})
        if u.path == "/open-api/v2/rest/devices":
            if self.authed():
                with LOCK:
                    self.send({"error": 0, "data": {"device_list": DEVICES}, "message": "success"})
            return
        if u.path == "/open-api/v2/sse/bridge":
            if parse_qs(u.query).get("access_token", [""])[0] != TOKEN:
                return self.send({"error": 401, "data": {}, "message": "invalid access_token"})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            q = queue.Queue()
            LISTENERS.append(q)
            try:
                while True:
                    ev, data = q.get()
                    self.wfile.write(f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode())
                    self.wfile.flush()
            except Exception:
                LISTENERS.remove(q)
            return
        self.send({"error": 404, "data": {}, "message": "not found"}, 404)

    def do_PUT(self):
        u = urlparse(self.path)
        prefix = "/open-api/v2/rest/devices/"
        if not u.path.startswith(prefix) or not self.authed():
            return
        sn = u.path[len(prefix):]
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with LOCK:
            LOG.append((sn, body))
            dev = next((d for d in DEVICES if d["serial_number"] == sn), None)
            if not dev:
                return self.send({"error": 110000, "data": {}, "message": "device not exist"})
            if "name" in body:
                dev["name"] = body["name"]
            if "tags" in body:  # real iHost: shallow merge of top-level keys, null clears the value
                dev["tags"] = {**(dev.get("tags") or {}), **body["tags"]}
            st = body.get("state") or {}
            if "power" in st and st["power"].get("powerState") == "toggle":
                st = copy.deepcopy(st)
                st["power"]["powerState"] = flip(dev["state"]["power"]["powerState"])
            if "toggle" in st:
                st = copy.deepcopy(st)
                for ch, v in st["toggle"].items():
                    if v.get("toggleState") == "toggle":
                        v["toggleState"] = flip(dev["state"]["toggle"][ch]["toggleState"])
            merge(dev["state"], st)
            if st:
                for q in list(LISTENERS):
                    q.put(("device#v2#updateDeviceState", {"endpoint": {"serial_number": sn}, "payload": st}))
            self.send({"error": 0, "data": dev, "message": "success"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"mock iHost on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
