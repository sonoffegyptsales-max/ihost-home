// Local stand-in for Vercel: serves static/ and routes /api/* to api/index.mjs (Web Request/Response).
//   APP_PASSWORD=... IHOST_URL=... IHOST_TOKEN=... node tests/vercel_local.mjs 8098
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as api from "../api/index.mjs";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "static");
const types = { ".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json" };
const port = Number(process.argv[2] || 8098);

http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  if (url.pathname.startsWith("/api/")) {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const request = new Request(url, { method: req.method, headers: req.headers, body: ["GET", "HEAD"].includes(req.method) ? undefined : Buffer.concat(chunks) });
    const response = await api[req.method](request);
    res.writeHead(response.status, Object.fromEntries(response.headers));
    if (response.body) for await (const chunk of response.body) res.write(chunk);
    return res.end();
  }
  const file = path.join(root, url.pathname === "/" ? "index.html" : url.pathname);
  if (!file.startsWith(root) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { "Content-Type": types[path.extname(file)] || "application/octet-stream" });
  res.end(fs.readFileSync(file));
}).listen(port, () => console.log(`vercel-local on http://127.0.0.1:${port}`));
