"use strict";
const $ = (s) => document.querySelector(s);
let S = null, editing = null, pollTimer = null;

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  let data = {};
  try { data = await r.json(); } catch (_) {}
  if (!r.ok || data.error) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}
function msg(el, text, kind) { el.textContent = text || ""; el.className = "hint" + (kind ? " " + kind : ""); }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

async function refresh() {
  try { S = await api("GET", "/api/status"); } catch (e) { msg($("#pairMsg"), e.message, "err"); return; }
  $("#ver").textContent = "v" + S.version + (S.fake ? " · TEST MODE" : "");
  $("#svc").textContent = S.service_address;
  if (document.activeElement !== $("#ihostHost")) $("#ihostHost").value = S.ihost.host;
  const b = $("#ihostBadge");
  if (S.ihost.paired && S.ihost.bridge) { b.textContent = "connected"; b.className = "badge ok";
    $("#ihostInfo").textContent = `iHost ${S.ihost.bridge.hostname || ""} · ${S.ihost.bridge.ip || ""} · firmware ${S.ihost.bridge.fw_version || "?"}`; }
  else if (S.ihost.paired) { b.textContent = "unreachable"; b.className = "badge bad"; $("#ihostInfo").textContent = S.ihost.error || ""; }
  else { b.textContent = "not connected"; b.className = "badge"; $("#ihostInfo").textContent = "Press Connect, then click Done on the iHost web console within 5 minutes."; }
  $("#pairBtn").textContent = S.ihost.paired ? "Reconnect" : "Connect";
  const p = S.pair;
  if (p.state === "waiting") msg($("#pairMsg"), "⏳ " + p.message);
  else if (p.state === "done") msg($("#pairMsg"), "✓ " + p.message, "ok");
  else if (p.state === "error" || p.state === "timeout") msg($("#pairMsg"), p.message, "err");
  if (!$("#apiKey").value && S.tuya.api_key) { $("#apiKey").value = S.tuya.api_key; $("#region").value = S.tuya.region; $("#anyDev").value = S.tuya.device_id; }
  if (S.tuya.has_secret) $("#apiSecret").placeholder = "saved (leave empty to keep)";
  $("#scanBtn").disabled = S.scan.state === "scanning";
  msg($("#scanMsg"), S.scan.message, S.scan.state === "error" ? "err" : "");
  renderDevices();
  clearTimeout(pollTimer);
  pollTimer = setTimeout(refresh, p.state === "waiting" || S.scan.state === "scanning" ? 1500 : 4000);
}

function stateText(d) {
  if (d.special === "ac") { const a = d.ac || {}; return a.power === "on" ? `${a.mode} ${a.temp}° · fan ${a.fan}` : "off"; }
  if (!d.state) return "";
  const st = d.state;
  if (st.toggle) return Object.entries(st.toggle).map(([c, v]) => `${c}:${v.toggleState}`).join(" ");
  let t = st.power ? st.power.powerState : "";
  if (st.brightness) t += ` ${st.brightness.brightness}%`;
  return t;
}

function renderDevices() {
  const box = $("#devices");
  if (!S.devices.length) { box.innerHTML = '<p class="empty">No Tuya devices yet. Import them in step 2, then Scan.</p>'; return; }
  box.innerHTML = S.devices.map((d) => {
    const problems = [];
    const sp = d.special;
    if (!d.has_key && sp !== "ac" && sp !== "lock") problems.push('<span class="pill bad">no local key</span>');
    if (!d.ip && !sp) problems.push('<span class="pill warn">IP unknown – scan</span>');
    if (sp === "ir_hub" && !d.ip) problems.push('<span class="pill warn">IP unknown – scan</span>');
    if (sp === "ac" && !d.hub) problems.push('<span class="pill bad">pick IR transmitter (Edit)</span>');
    if (sp === "ac" && d.last_error) problems.push(`<span class="pill bad" title="${esc(d.last_error)}">last command failed</span>`);
    let live = "";
    if (sp === "lock") {
      const lk = d.locked === true ? '<span class="pill on">locked</span>' : d.locked === false ? '<span class="pill warn">UNLOCKED</span>' : "";
      live = `${lk}${d.battery != null ? ` <span class="pill">battery ${esc(d.battery)}%</span>` : ""}${d.in_ihost && d.online === false ? ' <span class="pill bad">cloud unreachable</span>' : ""}`;
      if (d.unlock_in_ihost) live += ' <span class="pill bad">unlock switch in iHost</span>';
    }
    else if (sp === "ir_hub" && d.online != null) live = d.online ? '<span class="pill on">online</span>' : '<span class="pill bad">offline</span>';
    else if (d.in_ihost) live = d.online === false ? '<span class="pill bad">offline</span>'
      : `<span class="pill ${(sp === "ac" ? d.ac && d.ac.power === "on" : d.state && d.state.power && d.state.power.powerState === "on") ? "on" : "off"}">${esc(stateText(d)) || "…"}</span>`;
    const kindName = { ac: "IR air conditioner", ir_hub: "IR transmitter", lock: "door lock", ir_other: "IR remote" }[sp] || d.kind;
    const kind = `${kindName}${d.channels > 1 ? ` · ${d.channels} gangs` : ""}${d.dimmable ? " · dimmable" : ""}`;
    const can = sp === "ac" ? !!d.hub : sp === "lock" ? !!d.cloud : (d.has_key && (d.ip || S.fake));
    const testable = can;
    let extra = "";
    if (sp === "lock") {
      const ago = d.checked ? `${Math.max(0, Math.round((Date.now() / 1000 - d.checked) / 60))} min ago` : "never";
      const lu = d.last_unlock ? ` · last iHost unlock: ${new Date(d.last_unlock.at * 1000).toLocaleString()} ${d.last_unlock.ok ? "✓" : "✗ " + esc(d.last_unlock.error || "")}` : "";
      const rt = d.realtime || {};
      const rtText = rt.state === "connected" ? '<b class="oktxt">real-time (instant)</b>'
        : rt.state === "unavailable" ? `<span class="errtxt" title="${esc(rt.error || "")}">real-time not allowed by Tuya – subscribe "Message Service"</span>`
        : rt.state === "error" ? `<span class="errtxt" title="${esc(rt.error || "")}">real-time reconnecting</span>` : "real-time starting…";
      const lastOpen = d.last_unlocked ? `Last unlocked: <b>${esc(new Date(d.last_unlocked.at * 1000).toLocaleString())}</b> (${esc(d.last_unlocked.method)})` : "No unlock seen yet";
      extra = `<div class="meta">${lastOpen}</div>
        <div class="meta">Updates: ${rtText} · checked ${esc(ago)} (every ${esc(d.poll_minutes)} min without real-time)${d.lock_error ? ` · <span class="errtxt">${esc(d.lock_error)}</span>` : ""}${lu}</div>
        <div class="meta lockrow">${d.unlock_enabled
          ? `Unlock from the iHost: <b>allowed</b> · ${d.unlock_in_ihost ? '<button class="danger" data-act="remove-unlock">Remove unlock switch</button>' : '<button data-act="add-unlock">Add unlock switch to iHost</button>'}
             <button class="ghost" data-act="disallow-unlock">Disallow</button>`
          : `Unlock from the iHost: off · <button class="ghost" data-act="allow-unlock" ${d.cloud ? "" : "disabled"}>Allow…</button>`}</div>`;
      if (!d.cloud) extra += '<div class="meta note">Enter the Tuya cloud keys in step 2 first.</div>';
    }
    if (sp === "ac") {
      extra = `<div class="meta">IR transmitter: ${esc(d.hub ? d.hub.name : "—")} · learned codes: ${d.codes.length ? esc(d.codes.join(", ")) : "none"}
        · fallback: ${d.cloud ? "Tuya cloud" : "none (learn codes)"}${d.via ? ` · last sent: ${esc(d.via)}` : ""}</div>`;
    }
    if (d.note) extra += `<div class="meta note">${esc(d.note)}</div>`;
    return `<div class="dev" data-id="${esc(d.id)}">
      <div><h3>${esc(d.name || d.id)} ${d.in_ihost ? '<span class="pill on">in iHost</span>' : ""} ${live} ${problems.join(" ")}</h3>
        <div class="meta">${esc(kind)} · ${esc(d.product || d.category || "")} · ${esc(d.ip || "no IP")} · v${esc(d.version || "?")} · <code>${esc(d.id)}</code></div>${extra}</div>
      <div class="acts">
        ${sp === "ac" ? '<button class="ghost" data-act="codes">IR codes</button>' : ""}
        ${d.in_ihost ? `${sp === "ir_hub" || sp === "lock" ? "" : '<button class="ghost" data-act="toggle">Toggle</button>'}<button class="danger" data-act="remove">Remove from iHost</button>`
                     : (d.addable ? `<button data-act="add" ${can && S.ihost.paired ? "" : "disabled"}>${sp === "lock" ? "Add status sensor to iHost" : "Add to iHost"}</button>` : "")}
        ${testable ? `<button class="ghost" data-act="test">${sp === "lock" ? "Check now" : "Test"}</button>` : ""}
        <button class="ghost" data-act="edit">Edit</button>
        ${d.in_ihost ? "" : '<button class="danger" data-act="delete">Forget</button>'}
      </div></div>`;
  }).join("");
  if (codesFor) renderCodes();
}

// ---- IR code learning (AC)
let codesFor = null;
const MODES = ["cool", "heat", "auto", "fan", "dry"];
function renderCodes() {
  const d = S.devices.find((x) => x.id === codesFor);
  if (!d) return;
  $("#cTitle").textContent = `IR codes – ${d.name}`;
  const L = d.learn || {};
  msg($("#cMsg"), L.message || "", L.state === "error" ? "err" : L.state === "done" ? "ok" : "");
  $("#cLearn").disabled = L.state === "waiting";
  $("#cList").innerHTML = d.codes.length ? d.codes.map((k) => `<div class="code-row"><code>${esc(k)}</code>
      <span><button type="button" class="ghost" data-code-send="${esc(k)}">Send</button>
      <button type="button" class="danger" data-code-del="${esc(k)}">Delete</button></span></div>`).join("")
    : '<p class="muted">No codes learned yet.</p>';
}
function codeKey() {
  const m = $("#cMode").value;
  if (m === "off") return "off";
  if (m === "fan" || m === "dry") return m;
  return `${m}_${$("#cTemp").value}`;
}
$("#cMode").addEventListener("change", () => { $("#cTemp").disabled = ["off", "fan", "dry"].includes($("#cMode").value); });
$("#cLearn").addEventListener("click", async () => {
  try { await api("POST", `/api/devices/${encodeURIComponent(codesFor)}/learn`, { key: codeKey() }); } catch (e) { msg($("#cMsg"), e.message, "err"); }
  refresh();
});
$("#cList").addEventListener("click", async (e) => {
  const s = e.target.closest("[data-code-send]"), del = e.target.closest("[data-code-del]");
  try {
    if (s) { await api("POST", `/api/devices/${encodeURIComponent(codesFor)}/send-code`, { key: s.dataset.codeSend }); msg($("#cMsg"), `Sent '${s.dataset.codeSend}'`, "ok"); }
    if (del && confirm(`Delete code '${del.dataset.codeDel}'?`)) await api("POST", `/api/devices/${encodeURIComponent(codesFor)}/delete-code`, { key: del.dataset.codeDel });
  } catch (err) { msg($("#cMsg"), err.message, "err"); }
  refresh();
});
$("#codesDlg").addEventListener("close", () => { codesFor = null; });
(() => { const t = $("#cTemp"); for (let i = 16; i <= 30; i++) t.insertAdjacentHTML("beforeend", `<option ${i === 24 ? "selected" : ""}>${i}</option>`); })();

$("#devices").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]"); if (!btn) return;
  const id = btn.closest(".dev").dataset.id, act = btn.dataset.act, d = S.devices.find((x) => x.id === id);
  const out = $("#scanMsg");
  btn.disabled = true;
  try {
    if (act === "add") { await api("POST", `/api/devices/${encodeURIComponent(id)}/add`); msg(out, `✓ ${d.name} added to the iHost`, "ok"); }
    if (act === "remove" && confirm(`Remove "${d.name}" from the iHost? (Scenes using it will break.)`)) { await api("POST", `/api/devices/${encodeURIComponent(id)}/remove`); msg(out, "Removed from the iHost"); }
    if (act === "delete" && confirm(`Forget "${d.name}"?`)) await api("DELETE", `/api/devices/${encodeURIComponent(id)}`);
    if (act === "test") {
      const r = await api("POST", `/api/devices/${encodeURIComponent(id)}/test`);
      msg(out, r.ok ? `✓ ${d.name} answered: ${JSON.stringify(r.dps)}` : `✗ ${d.name}: ${r.error}. Check the IP, and try another protocol version in Edit.`, r.ok ? "ok" : "err");
    }
    if (act === "toggle") { const r = await api("POST", `/api/devices/${encodeURIComponent(id)}/set`, { state: { power: { powerState: "toggle" } } });
      if (!r.ok) msg(out, `✗ ${d.name}: device didn't answer`, "err"); }
    if (act === "codes") { codesFor = id; renderCodes(); $("#codesDlg").showModal(); }
    if (act === "allow-unlock") {
      allowFor = id;
      $("#aName").textContent = d.name;
      $("#aWord").value = "";
      msg($("#aMsg"), "");
      $("#allowDlg").showModal();
      setTimeout(() => $("#aWord").focus(), 50);
    }
    if (act === "disallow-unlock") { await api("POST", `/api/devices/${encodeURIComponent(id)}/unlock-enable`, { enabled: false }); msg(out, "Unlock from the iHost turned off (switch removed)."); }
    if (act === "add-unlock") { await api("POST", `/api/devices/${encodeURIComponent(id)}/add-unlock`); msg(out, `"${d.name} Unlock" switch added to the iHost`, "ok"); }
    if (act === "remove-unlock" && confirm("Remove the unlock switch from the iHost?")) { await api("POST", `/api/devices/${encodeURIComponent(id)}/remove-unlock`); msg(out, "Unlock switch removed"); }
    if (act === "edit") {
      editing = d; $("#eName").value = d.name || ""; $("#eIp").value = d.ip || ""; $("#eVer").value = d.version || "3.3"; $("#eDps").value = "";
      const hubs = S.devices.filter((x) => x.special === "ir_hub");
      $("#eHubRow").hidden = d.special !== "ac";
      $("#eHub").innerHTML = hubs.map((h) => `<option value="${esc(h.id)}" ${d.hub && d.hub.id === h.id ? "selected" : ""}>${esc(h.name)}</option>`).join("");
      $("#editDlg").showModal();
    }
  } catch (err) { msg(out, err.message, "err"); }
  btn.disabled = false;
  refresh();
});

$("#editDlg").addEventListener("close", async () => {
  if ($("#editDlg").returnValue !== "save" || !editing) return;
  const body = { name: $("#eName").value, ip: $("#eIp").value, version: $("#eVer").value };
  if ($("#eDps").value.trim()) body.dps_channels = $("#eDps").value;
  if (!$("#eHubRow").hidden && $("#eHub").value) body.ir_hub = $("#eHub").value;
  try { await api("POST", `/api/devices/${encodeURIComponent(editing.id)}/edit`, body); } catch (e) { msg($("#scanMsg"), e.message, "err"); }
  editing = null; refresh();
});

$("#ihostForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("POST", "/api/ihost", { host: $("#ihostHost").value }); } catch (err) { msg($("#pairMsg"), err.message, "err"); }
  refresh();
});
$("#cloudForm").addEventListener("submit", async (e) => {
  e.preventDefault(); const m = $("#importMsg"); msg(m, "Contacting the Tuya cloud…");
  try { const r = await api("POST", "/api/import/cloud", { region: $("#region").value, api_key: $("#apiKey").value, api_secret: $("#apiSecret").value, device_id: $("#anyDev").value });
    $("#apiSecret").value = ""; msg(m, `✓ Imported – ${r.added} new, ${r.total} total. Now press Scan.`, "ok"); }
  catch (err) { msg(m, err.message, "err"); }
  refresh();
});
$("#jsonForm").addEventListener("submit", async (e) => {
  e.preventDefault(); const m = $("#importMsg");
  try { const r = await api("POST", "/api/import/json", { devices: $("#jsonText").value }); $("#jsonText").value = ""; msg(m, `✓ Imported – ${r.added} new, ${r.total} total. Now press Scan.`, "ok"); }
  catch (err) { msg(m, err.message, "err"); }
  refresh();
});
$("#scanBtn").addEventListener("click", async () => { try { await api("POST", "/api/scan"); } catch (e) { msg($("#scanMsg"), e.message, "err"); } refresh(); });
refresh();

// ---- "Allow unlock from the iHost" dialog (prompt() is blocked in many in-app browsers)
let allowFor = null;
$("#allowForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const word = $("#aWord").value.trim();
  if (word.toUpperCase() !== "UNLOCK") { msg($("#aMsg"), "Type the word UNLOCK to confirm.", "err"); return; }
  try {
    await api("POST", `/api/devices/${encodeURIComponent(allowFor)}/unlock-enable`, { enabled: true, confirm: word });
    $("#allowDlg").close();
    msg($("#scanMsg"), "Unlock from the iHost allowed – now press “Add unlock switch to iHost”.", "ok");
  } catch (err) { msg($("#aMsg"), err.message, "err"); }
  refresh();
});
$("#aCancel").addEventListener("click", () => $("#allowDlg").close());
