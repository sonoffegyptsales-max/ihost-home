// iHost Home - cloud API for Vercel.
//
// Talks to iHosts over their eWeLink remote address (https://<deviceid>.cube.ewelink.cc), which passes
// the Open API V2 through, so no computer has to run at home. The iHost token lives only in Vercel
// environment variables; browsers talk to this function and must sign in with APP_PASSWORD.
//
// Environment variables
//   APP_PASSWORD   required, 10+ characters - the sign-in password for the app
//   IHOST_URL      e.g. https://1001ec4e2e.cube.ewelink.cc      (or IHOSTS for several, see below)
//   IHOST_TOKEN    the iHost Open API token
//   IHOST_NAME     optional home name
//   IHOSTS         optional JSON: [{"name":"Villa","url":"https://...cube.ewelink.cc","token":"..."}, ...]
//   SESSION_SECRET optional; change it to sign every device out
import crypto from "node:crypto";

const API = "/open-api/v2/rest";
const SESSION_DAYS = 90;
const HIDDEN_TAGS = new Set(["deviceInfo_built_in"]);
const UI_TAG = "ihh_ui"; // favourites + icons, stored on the iHost so every phone sees the same
const fails = new Map();  // best-effort per-instance login throttle

// ------------------------------------------------------------------ config
function hubs() {
  let list = [];
  try { if (process.env.IHOSTS) list = JSON.parse(process.env.IHOSTS); } catch { list = []; }
  if (!list.length && process.env.IHOST_URL && process.env.IHOST_TOKEN) {
    list = [{ name: process.env.IHOST_NAME || "My Home", url: process.env.IHOST_URL, token: process.env.IHOST_TOKEN }];
  }
  return list.filter((h) => h && h.url && h.token).map((h) => {
    const url = String(h.url).trim().replace(/\/+$/, "").replace(/\/\?.*$/, "");
    return { id: crypto.createHash("sha256").update(url).digest("hex").slice(0, 8), name: h.name || "My Home",
             url: /^https?:\/\//.test(url) ? url : "https://" + url, token: String(h.token).trim() };
  });
}
const password = () => process.env.APP_PASSWORD || "";
function secret() {
  // changing APP_PASSWORD or SESSION_SECRET signs everyone out
  return crypto.createHash("sha256").update(`${process.env.SESSION_SECRET || ""}|${password()}|${hubs().map((h) => h.token).join(",")}`).digest();
}

// ------------------------------------------------------------------ http helpers
const SEC_HEADERS = { "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store" };
function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...SEC_HEADERS, ...extra } });
}
function cookieHeader(value, maxAge) {
  return `ihh=${value}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAge}`;
}
function newSession() {
  const exp = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86400;
  const body = `${exp}.${crypto.randomBytes(12).toString("hex")}`;
  const sig = crypto.createHmac("sha256", secret()).update(body).digest("hex");
  return `${body}.${sig}`;
}
function validSession(req) {
  const m = (req.headers.get("cookie") || "").match(/(?:^|;\s*)ihh=([^;]+)/);
  if (!m) return false;
  const [exp, nonce, sig] = m[1].split(".");
  if (!exp || !nonce || !sig || Number(exp) < Date.now() / 1000) return false;
  const good = crypto.createHmac("sha256", secret()).update(`${exp}.${nonce}`).digest("hex");
  return sig.length === good.length && crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(good));
}
function sameOrigin(req) {
  const origin = req.headers.get("origin");
  if (!origin) return true;
  try { return new URL(origin).host === new URL(req.url).host; } catch { return false; }
}
const clientIp = (req) => (req.headers.get("x-forwarded-for") || "").split(",")[0].trim() || "?";
async function body(req) {
  try { const t = await req.text(); return t ? JSON.parse(t) : {}; } catch { return {}; }
}

// ------------------------------------------------------------------ iHost client
class GatewayError extends Error {}
async function ihost(hub, method, path, payload, timeoutMs = 12000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(hub.url + API + path, {
      method, signal: ctl.signal,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${hub.token}` },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    const text = await res.text();
    try { return JSON.parse(text || "{}"); } catch { throw new GatewayError(`iHost sent an invalid reply (${res.status})`); }
  } catch (e) {
    if (e instanceof GatewayError) throw e;
    throw new GatewayError(`Can't reach the iHost through eWeLink (${e.name === "AbortError" ? "timeout" : e.message}). Is it online?`);
  } finally { clearTimeout(timer); }
}
async function deviceList(hub, strip = true) {
  const res = await ihost(hub, "GET", "/devices");
  if (res.error !== 0) throw new GatewayError(res.error === 401 ? "The iHost rejected the token - pair again and update IHOST_TOKEN" : `iHost error ${res.error}: ${res.message}`);
  const devs = (res.data && res.data.device_list) || [];
  if (strip) for (const d of devs) d.tags = Object.fromEntries(Object.entries(d.tags || {}).filter(([k]) => !HIDDEN_TAGS.has(k)));
  return devs;
}
// The iHost shallow-merges top-level tag keys and clears a key only when it's sent as null (fw 2.14.1).
async function mergeTags(hub, sn, patch, devs) {
  devs = devs || await deviceList(hub, false);
  const dev = devs.find((d) => d.serial_number === sn);
  if (!dev) throw new GatewayError("device not found");
  const tags = { ...(dev.tags || {}) };
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) tags[k] = null;
    else if (k === "toggle" && v && typeof v === "object") {
      const names = { ...(tags.toggle || {}) };
      for (const [ch, name] of Object.entries(v)) {
        if (name === null || name === "") delete names[String(ch)]; else names[String(ch)] = String(name).slice(0, 64);
      }
      tags.toggle = Object.keys(names).length ? names : null;
    } else tags[k] = v;
  }
  return ihost(hub, "PUT", `/devices/${encodeURIComponent(sn)}`, { tags });
}

// favourites/icons live in a tag on each device: {"fav": ["1","*"], "icon": {"1": "fan"}}  ("*" = whole device)
function prefsFrom(devs) {
  const favorites = [], icons = {};
  for (const d of devs) {
    const ui = (d.tags || {})[UI_TAG];
    if (!ui || typeof ui !== "object") continue;
    const key = (ch) => (ch === "*" ? d.serial_number : `${d.serial_number}:${ch}`);
    for (const ch of ui.fav || []) favorites.push(key(String(ch)));
    for (const [ch, ic] of Object.entries(ui.icon || {})) icons[key(ch)] = ic;
  }
  return { favorites, icons };
}
async function savePrefs(hub, want) {
  const devs = await deviceList(hub, false);
  const per = {};
  const split = (key) => { const i = key.indexOf(":"); return i < 0 ? [key, "*"] : [key.slice(0, i), key.slice(i + 1)]; };
  for (const d of devs) per[d.serial_number] = { fav: [], icon: {} };
  for (const k of want.favorites || []) { const [sn, ch] = split(k); if (per[sn]) per[sn].fav.push(ch); }
  for (const [k, ic] of Object.entries(want.icons || {})) { const [sn, ch] = split(k); if (per[sn]) per[sn].icon[ch] = String(ic).slice(0, 16); }
  const jobs = [];
  for (const d of devs) {
    const next = per[d.serial_number];
    const empty = !next.fav.length && !Object.keys(next.icon).length;
    const cur = (d.tags || {})[UI_TAG] || null;
    const nextVal = empty ? null : next;
    if (JSON.stringify(cur) !== JSON.stringify(nextVal)) jobs.push(mergeTags(hub, d.serial_number, { [UI_TAG]: nextVal }, devs));
  }
  await Promise.all(jobs);
  const after = await deviceList(hub);
  return prefsFrom(after);
}

// ------------------------------------------------------------------ live events: relay the iHost stream for ~55 s
function events(hub) {
  const enc = new TextEncoder();
  const ctl = new AbortController();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (s) => { try { controller.enqueue(enc.encode(s)); } catch { /* closed */ } };
      const stop = setTimeout(() => ctl.abort(), 55000); // stay under the function time limit; the browser reconnects
      const ping = setInterval(() => send(": ping\n\n"), 15000);
      send(": " + " ".repeat(2048) + "\nretry: 1000\n\n");
      try {
        const res = await fetch(`${hub.url}/open-api/v2/sse/bridge?access_token=${encodeURIComponent(hub.token)}`,
          { headers: { Accept: "text/event-stream" }, signal: ctl.signal });
        if (!res.ok || !res.body) throw new Error(`status ${res.status}`);
        send(`event: gateway\ndata: ${JSON.stringify({ connected: true })}\n\n`);
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.search(/\r?\n\r?\n/)) >= 0) {
            const block = buf.slice(0, i);
            buf = buf.slice(i).replace(/^\r?\n\r?\n/, "");
            let ev = null; const data = [];
            for (const line of block.split(/\r?\n/)) {
              if (line.startsWith("event:")) ev = line.slice(6).trim();
              else if (line.startsWith("data:")) data.push(line.slice(5).trim());
            }
            if (!ev && !data.length) continue;
            let payload; try { payload = JSON.parse(data.join("\n")); } catch { payload = { raw: data.join("\n") }; }
            send(`event: ihost\ndata: ${JSON.stringify({ type: ev, data: payload })}\n\n`);
          }
        }
      } catch (e) {
        if (e.name !== "AbortError") send(`event: gateway\ndata: ${JSON.stringify({ connected: false })}\n\n`);
      } finally {
        clearTimeout(stop); clearInterval(ping);
        try { controller.close(); } catch { /* already closed */ }
      }
    },
    cancel() { ctl.abort(); },
  });
  return new Response(stream, { headers: { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff" } });
}

// ------------------------------------------------------------------ router
async function handle(req) {
  const url = new URL(req.url);
  const method = req.method.toUpperCase();
  const path = "/api/" + (url.searchParams.get("__path") || url.pathname.replace(/^\/api\/?/, "")).replace(/^\/+/, "");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent); // ['api', ...]
  const raw = method === "GET" || method === "HEAD" ? {} : await body(req); // always drain the body
  try {
    if (method !== "GET" && !sameOrigin(req)) return json({ error: "cross-origin request blocked" }, 403);

    const pw = password();
    const configured = pw.length >= 10 && hubs().length > 0;
    if (path === "/api/session") {
      return json({ authed: configured && validSession(req), login_required: true, remote: true, cloud: true,
                    has_password: !!pw, remote_enabled: true,
                    setup: configured ? null : (pw.length < 10 ? "Set APP_PASSWORD (at least 10 characters) in the Vercel project settings, then redeploy."
                                                              : "Set IHOST_URL and IHOST_TOKEN in the Vercel project settings, then redeploy.") });
    }
    if (!configured) return json({ error: "not set up" }, 503);
    if (path === "/api/login" && method === "POST") {
      const ip = clientIp(req);
      const recent = (fails.get(ip) || []).filter((t) => Date.now() - t < 15 * 60 * 1000);
      if (recent.length >= 5) return json({ error: "Too many attempts - try again later" }, 429);
      const given = Buffer.from(String(raw.password || ""));
      const want = Buffer.from(pw);
      const ok = given.length === want.length && crypto.timingSafeEqual(given, want);
      if (ok) { fails.delete(ip); return json({ ok: true }, 200, { "Set-Cookie": cookieHeader(newSession(), SESSION_DAYS * 86400) }); }
      fails.set(ip, [...recent, Date.now()]);
      await new Promise((r) => setTimeout(r, 600));
      return json({ error: "Wrong password" }, 403);
    }
    if (path === "/api/logout" && method === "POST") return json({ ok: true }, 200, { "Set-Cookie": cookieHeader("", 0) });
    if (!validSession(req)) return json({ error: "login required" }, 401);

    if (path === "/api/hubs" && method === "GET") {
      return json({ hubs: hubs().map((h) => ({ id: h.id, name: h.name, host: new URL(h.url).host, deviceid: new URL(h.url).host.split(".")[0], live: true })) });
    }
    if (path === "/api/security" && method === "GET") {
      return json({ cloud: true, has_password: true, weak_password: false, lan_login: false, has_link: false, this_request_remote: true,
                    remote: { enabled: true, mode: "cloud", state: "running", url: url.origin, error: "" } });
    }
    if (parts[1] === "hubs" && parts[2]) {
      const hub = hubs().find((h) => h.id === parts[2]);
      if (!hub) return json({ error: "unknown iHost" }, 404);
      const rest = parts.slice(3);
      if (rest[0] === "status" && method === "GET") {
        let bridge = null, error = null;
        try { bridge = (await ihost(hub, "GET", "/bridge", undefined, 8000)).data; } catch (e) { error = e.message; }
        return json({ hub: { id: hub.id, name: hub.name, host: new URL(hub.url).host }, bridge, error, live: true });
      }
      if (rest[0] === "devices" && rest.length === 1 && method === "GET") return json({ devices: await deviceList(hub) });
      if (rest[0] === "devices" && rest.length === 2 && method === "PUT") {
        const payload = {};
        if (raw.state) payload.state = raw.state;
        if ("name" in raw) {
          const name = String(raw.name).trim();
          if (!name || name.length > 64) return json({ error: "name must be 1-64 characters" }, 400);
          payload.name = name;
        }
        if (!Object.keys(payload).length) return json({ error: "nothing to update" }, 400);
        const res = await ihost(hub, "PUT", `/devices/${encodeURIComponent(rest[1])}`, payload);
        return json(res, res.error === 0 ? 200 : 502);
      }
      if (rest[0] === "devices" && rest[2] === "tags" && method === "PATCH") {
        const res = await mergeTags(hub, rest[1], raw);
        return json(res, res.error === 0 ? 200 : 502);
      }
      if (rest[0] === "prefs") {
        if (method === "GET") return json(prefsFrom(await deviceList(hub)));
        if (method === "PUT") return json(await savePrefs(hub, { ...prefsFrom(await deviceList(hub)), ...raw }));
      }
      if (rest[0] === "events" && method === "GET") return events(hub);
      if (rest.length === 0 && (method === "PATCH" || method === "DELETE")) {
        return json({ error: "In the cloud version, homes are set in the Vercel environment variables (IHOSTS / IHOST_NAME)." }, 403);
      }
    }
    if (["/api/scan", "/api/pair", "/api/remote"].includes(path) || path.startsWith("/api/security/")) {
      return json({ error: "Not available in the cloud version - use the home version (python server.py) for this." }, 403);
    }
    return json({ error: "not found" }, 404);
  } catch (e) {
    return json({ error: e instanceof GatewayError ? e.message : `Server error: ${e.message}` }, 502);
  }
}

export const GET = handle, POST = handle, PUT = handle, PATCH = handle, DELETE = handle;
