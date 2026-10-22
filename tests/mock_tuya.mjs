// Mock Tuya OpenAPI for door-lock tests. Verifies every request's signature exactly like Tuya does.
//   node tests/mock_tuya.mjs 8767     (client id "mockid", secret "mocksecret", lock "lockdev1")
import http from "node:http";
import crypto from "node:crypto";

const ID = "mockid", SECRET = "mocksecret", TOKEN = "mocktoken", LOCK_ID = "lockdev1";
const calls = [];
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
function expected(method, path, body, token, t) {
  const sts = [method, sha(body || ""), "", path].join("\n");
  return crypto.createHmac("sha256", SECRET).update(ID + (token || "") + t + sts).digest("hex").toUpperCase();
}
let motor = false; // false = locked
http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks).toString();
  const send = (obj) => { res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(obj)); };
  const p = req.url;
  if (p === "/__calls") return send(calls);
  const isToken = p.startsWith("/v1.0/token");
  const tok = isToken ? "" : req.headers.access_token;
  if (req.headers.client_id !== ID) return send({ success: false, code: 1005, msg: "clientId is invalid" });
  if (req.headers.sign !== expected(req.method, p, body, tok, req.headers.t)) return send({ success: false, code: 1004, msg: "sign invalid" });
  if (!isToken && tok !== TOKEN) return send({ success: false, code: 1010, msg: "token invalid" });
  calls.push({ method: req.method, path: p, body });
  if (isToken) return send({ success: true, result: { access_token: TOKEN, expire_time: 7200, uid: "u1" } });
  const base = `/v1.0/devices/${LOCK_ID}`;
  if (p === base) return send({ success: true, result: { id: LOCK_ID, name: "Front Door", online: true, category: "videolock", product_name: "Smart Lock",
    update_time: Math.floor(Date.now() / 1000) - 300, status: [{ code: "residual_electricity", value: 87 }, { code: "lock_motor_state", value: motor },
      { code: "closed_opened", value: "closed" }, { code: "unlock_fingerprint", value: 3 }, { code: "doorbell", value: false }, { code: "beep_volume", value: "normal" }] } });
  if (p.startsWith(`${base}/door-lock/open-logs?`)) return send({ success: true, result: { total: 2, logs: [
    { update_time: Date.now() - 3600e3, nick_name: "Magdy", status: [{ code: "unlock_fingerprint", value: 3 }] },
    { update_time: Date.now() - 7200e3, status: [{ code: "unlock_password", value: 1 }] }] } });
  if (p.startsWith(`${base}/door-lock/alarm-logs?`)) return send({ success: true, result: { logs: [{ update_time: Date.now() - 86400e3, status: [{ code: "alarm_lock", value: "wrong_finger" }] }] } });
  if (p === `${base}/door-lock/remote-unlocks`) return send({ success: true, result: [{ remote_unlock_type: "remoteUnlockWithoutPwd", open: true }] });
  if (p === `${base}/door-lock/password-ticket` && req.method === "POST") return send({ success: true, result: { ticket_id: "tk1", ticket_key: "x", expire_time: 300 } });
  if (p === `${base}/door-lock/password-free/open-door` && req.method === "POST") {
    if (JSON.parse(body || "{}").ticket_id !== "tk1") return send({ success: false, code: 2001, msg: "bad ticket" });
    motor = true;
    return send({ success: true, result: true });
  }
  send({ success: false, code: 1108, msg: "uri path invalid" });
}).listen(Number(process.argv[2] || 8767), () => console.log("mock tuya up"));
