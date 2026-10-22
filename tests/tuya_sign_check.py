"""Cross-check api/index.mjs Tuya signing against tinytuya's (proven) implementation."""
import json, subprocess, sys, os
import tinytuya, requests

captured = []
class R:
    status_code = 200
    text = '{"success": true, "result": {}}'
    content = text.encode()
    def json(self): return json.loads(self.text)
def fake_get(url, headers=None, **kw): captured.append(("GET", url, dict(headers), "")); return R()
def fake_req(method, url, headers=None, data=None, **kw): captured.append((method, url, dict(headers), data or "")); return R()
requests.get = fake_get; requests.request = fake_req
tinytuya.Cloud.__init__.__defaults__  # noqa
c = tinytuya.Cloud(apiRegion="eu", apiKey="testclientid1234", apiSecret="testsecretabcdef0123456789", initial_token="TOKEN123")
c.cloudrequest("/v1.0/devices/bf6028af6ee6ed1adejezr/door-lock/open-logs", query={"start_time": 1, "page_size": 20, "end_time": 99, "page_no": 1})
c.cloudrequest("/v1.0/devices/bf6028af6ee6ed1adejezr")
cases = []
for method, url, h, body in captured:
    path = "/" + url.split("//", 1)[1].split("/", 1)[1]
    cases.append({"method": method, "path": path, "t": h["t"], "token": h.get("access_token", ""), "body": body, "sign": h["sign"]})
node = r"""
const { _test } = await import(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
const cfg = { id: "testclientid1234", secret: "testsecretabcdef0123456789" };
for (const c of cases) {
  const mine = _test.tuyaSign(cfg, c.method, c.path, c.body, c.token, c.t);
  console.log(mine === c.sign ? "MATCH" : "DIFF", c.method, c.path);
}
console.log("query builder:", _test.withQuery("/x", { start_time: 1, page_size: 20, end_time: 99, page_no: 1 }));
"""
api = "file:///" + os.path.abspath(sys.argv[1]).replace("\\", "/")
print(subprocess.run(["node.exe", "--input-type=module", "-e", node, api, json.dumps(cases)], capture_output=True, text=True).stdout
      or "node failed")
print("tinytuya signed paths:", [c["path"] for c in cases])
