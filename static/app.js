/* iHost Home — client. Vanilla JS, no build step.
   Data model: raw iHost device objects (from /api/hubs/<id>/devices) -> "accessories" (one per switch channel,
   one per single-relay device, one per sensor). Live updates arrive over /api/hubs/<id>/events (SSE). */
(() => {
  "use strict";

  // ------------------------------------------------------------------ icons
  const P = {
    light: '<path d="M12 2a7 7 0 0 0-4 12.74V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.26A7 7 0 0 0 12 2zM9 19.5h6V21a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1z"/>',
    switch: '<path fill-rule="evenodd" d="M8 2h8a3 3 0 0 1 3 3v14a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3V5a3 3 0 0 1 3-3zm1 2.5a1.5 1.5 0 0 0-1.5 1.5v5h9V6A1.5 1.5 0 0 0 15 4.5z"/>',
    outlet: '<path fill-rule="evenodd" d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20zM9 7.5a1 1 0 0 0-1 1v3a1 1 0 1 0 2 0v-3a1 1 0 0 0-1-1zm6 0a1 1 0 0 0-1 1v3a1 1 0 1 0 2 0v-3a1 1 0 0 0-1-1zm-3 7a1.6 1.6 0 0 0-1.6 1.6V17.5h3.2v-1.4A1.6 1.6 0 0 0 12 14.5z"/>',
    fan: '<path d="M12 10.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2zM11.1 2c2.8-.4 4.4 1.7 3.5 4.2-.4 1.2-1.3 2.3-1.6 3.6a2.6 2.6 0 0 0-2-.4C9.6 6.9 8.4 2.4 11.1 2zm9.8 11.3c-.6 2.8-3.1 3.6-5.1 1.8-.9-.8-1.6-2.2-2.6-3a2.6 2.6 0 0 0 .7-2c2.8.1 7.5 1.1 7 3.2zM5 18.8c-2.1-1.9-1.6-4.5.9-5.3 1.2-.4 2.7-.3 3.9-.7a2.6 2.6 0 0 0 1.3 1.6C9.7 16.8 6.6 20.3 5 18.8z"/>',
    motion: '<path d="M13.5 5.5a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM9.8 8.9 7 23h2.1l1.8-8 2.1 2v6h2v-7.5l-2.1-2 .6-3A7.3 7.3 0 0 0 19 13v-2a5 5 0 0 1-4.3-2.4l-1-1.6a2 2 0 0 0-2.5-.9L6 8.3V13h2V9.6z"/>',
    sensor: '<path fill-rule="evenodd" d="M14 14.76V4a2 2 0 1 0-4 0v10.76a4 4 0 1 0 4 0zM12 3a1 1 0 0 1 1 1v6h-2V4a1 1 0 0 1 1-1z"/>',
    blind: '<path d="M3 2h18v2h-1v14h-7v2.3a1.5 1.5 0 1 1-2 0V18H4V4H3zm3 2v2h12V4zm0 4v2h12V8zm0 4v2h12v-2z"/>',
    door: '<path d="M6 2h12a1 1 0 0 1 1 1v18h2v1H3v-1h2V3a1 1 0 0 1 1-1zm9 9.2a1 1 0 1 0 0 2 1 1 0 0 0 0-2z"/>',
    power: '<path d="M11 2h2v10h-2zM6.3 5.3l1.4 1.4A7 7 0 1 0 16.3 6.7l1.4-1.4A9 9 0 1 1 6.3 5.3z"/>',
    home: '<path d="M12 3 2 11h3v9h5v-6h4v6h5v-9h3z"/>',
    warn: '<path d="M12 2 1 21h22zm-1 7h2v6h-2zm0 8h2v2h-2z"/>',
    battery: '<path d="M2 8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v1h1a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-1v1a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2zm2 0v8h4V8z"/>',
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${P[name] || P.switch}</svg>`;
  const ACCENT = { blind: "#0a84ff", light: "#ff9f0a", switch: "#ff9f0a", outlet: "#30d158", fan: "#0a84ff", motion: "#ff9f0a", sensor: "#64d2ff", door: "#ff9f0a" };
  const DISPLAY_AS = [["light", "Light"], ["switch", "Switch"], ["outlet", "Outlet"], ["fan", "Fan"]];
  const DEFAULT_ROOM = "Default Room";
  const HOME = "__home";

  // ------------------------------------------------------------------ state
  const S = {
    devices: [],
    prefs: { favorites: [], icons: {} },
    hubs: [],         // every iHost this server knows
    hub: null,        // id of the iHost being shown
    session: {},      // {remote, login_required, authed, ...}
    security: {},     // password / sign-in link / internet access status
    status: null,
    view: HOME,
    live: false,
    busy: new Set(),
    sheet: null,      // {type, key?, sub?}
    opener: null,
    loaded: false,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ------------------------------------------------------------------ api
  class AuthError extends Error {}
  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    });
    let data = {};
    try { data = await res.json(); } catch { /* empty */ }
    if (res.status === 401) throw new AuthError("login required");
    if (!res.ok || (typeof data.error === "number" && data.error !== 0)) {
      throw new Error(typeof data.error === "string" ? data.error : (data.message || `Request failed (${res.status})`));
    }
    return data;
  }

  const hp = (p) => `/api/hubs/${encodeURIComponent(S.hub)}${p}`;
  const currentHub = () => S.hubs.find((h) => h.id === S.hub) || {};
  const homeName = () => currentHub().name || "My Home";

  // ------------------------------------------------------------------ model
  const caps = (d) => d.capabilities || [];
  const capSet = (d) => new Set(caps(d).map((c) => c.capability));
  const channelsOf = (d) => caps(d).filter((c) => c.capability === "toggle" && c.name != null).map((c) => String(c.name))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const roomOf = (d) => (d.tags && typeof d.tags.room === "string" && d.tags.room.trim()) || DEFAULT_ROOM;

  function defaultIcon(d) {
    const cat = (d.display_category || "").toLowerCase();
    if (cat.includes("light")) return "light";
    if (cat.includes("plug") || cat.includes("outlet") || cat.includes("socket")) return "outlet";
    if (cat.includes("fan")) return "fan";
    return "light"; // wall switches mostly drive lights
  }

  function sensorInfo(d) {
    const st = d.state || {};
    const c = capSet(d);
    let icon = "sensor", text = "", active = false, alert = false;
    if (c.has("motion")) {
      icon = "motion";
      active = !!(st.motion && st.motion.motion);
      text = active ? "Motion Detected" : "No Motion";
    } else if (c.has("contact")) {
      icon = "door";
      const v = st.contact && st.contact.contact;
      active = v === false;
      text = v === undefined ? "" : (v ? "Closed" : "Open");
    } else if (c.has("temperature") || c.has("humidity")) {
      const parts = [];
      if (st.temperature && st.temperature.temperature != null) parts.push(`${Math.round(st.temperature.temperature * 10) / 10}°`);
      if (st.humidity && st.humidity.humidity != null) parts.push(`${Math.round(st.humidity.humidity)}%`);
      text = parts.join(" · ");
    }
    const batt = st.battery && st.battery.battery;
    if (typeof batt === "number" && batt <= 20) { alert = true; text = "Low Battery"; }
    if (!text && typeof batt === "number") text = `Battery ${batt}%`;
    return { icon, text: text || "Sensor", active, alert, battery: batt };
  }

  /** Flatten devices into tiles */
  function accessories() {
    const out = [];
    for (const d of S.devices) {
      const chans = channelsOf(d);
      const c = capSet(d);
      const room = roomOf(d);
      const online = d.online !== false;
      const st = d.state || {};
      if (chans.length) {
        const names = (d.tags && d.tags.toggle) || {};
        for (const ch of chans) {
          const key = `${d.serial_number}:${ch}`;
          out.push({
            key, sn: d.serial_number, ch, kind: "switch", device: d, room, online,
            name: (names[ch] && String(names[ch])) || `${d.name} ${ch}`,
            on: !!(st.toggle && st.toggle[ch] && st.toggle[ch].toggleState === "on"),
            icon: S.prefs.icons[key] || defaultIcon(d), multi: chans.length > 1,
          });
        }
      } else if (c.has("power")) {
        const key = d.serial_number;
        out.push({
          key, sn: d.serial_number, ch: null, kind: "switch", device: d, room, online, name: d.name,
          on: !!(st.power && st.power.powerState === "on"), icon: S.prefs.icons[key] || defaultIcon(d), multi: false,
        });
      } else if (c.has("percentage")) {
        const key = d.serial_number;
        const pct = Number(st.percentage && st.percentage.percentage) || 0;
        out.push({ key, sn: d.serial_number, ch: null, kind: "switch", cover: true, pct, device: d, room, online, name: d.name,
          on: pct > 0, icon: "blind", multi: false });
      } else {
        const info = sensorInfo(d);
        out.push({ key: d.serial_number, sn: d.serial_number, ch: null, kind: "sensor", device: d, room, online, name: d.name,
          on: info.active, icon: info.icon, sensor: info, multi: false });
      }
    }
    return out;
  }
  const findAcc = (key) => accessories().find((a) => a.key === key);
  const findDev = (sn) => S.devices.find((d) => d.serial_number === sn);

  function rooms() {
    const set = new Set(S.devices.map(roomOf));
    const list = [...set].filter((r) => r !== DEFAULT_ROOM).sort((a, b) => a.localeCompare(b));
    if (set.has(DEFAULT_ROOM)) list.push(DEFAULT_ROOM);
    return list;
  }

  function statusText(a) {
    if (!a.online) return "No Response";
    if (a.kind === "sensor") return a.sensor.text;
    if (a.cover) return a.pct >= 100 ? "Open" : a.pct <= 0 ? "Closed" : `${a.pct}% Open`;
    return a.on ? "On" : "Off";
  }

  // ------------------------------------------------------------------ render: page
  function render() {
    $("#homeTitle").innerHTML = `${esc(homeName())}<svg class="title-chev" viewBox="0 0 12 8" aria-hidden="true"><path d="M1 1.5l5 5 5-5"/></svg>`;
    $("#homeTitle").setAttribute("aria-label", `${homeName()}, switch home`);
    document.title = homeName();
    $("#liveDot").classList.toggle("on", S.live);
    $("#liveDot").classList.toggle("poll", !S.live && !!S.polling);
    $("#liveDot").title = S.live ? "Live updates connected" : S.polling ? "Updating every 3 seconds" : "Live updates offline";
    renderNav();
    renderChips();
    renderContent();
    if (S.sheet) renderSheet();
  }

  function renderNav() {
    const nav = $("#roomsNav");
    const items = [[HOME, "Home"], ...rooms().map((r) => [r, r])];
    if (S.view !== HOME && !rooms().includes(S.view)) S.view = HOME;
    nav.innerHTML = items.map(([id, label]) =>
      `<button class="room-pill" data-room="${esc(id)}" aria-current="${id === S.view}">${esc(label)}</button>`).join("");
  }

  function renderChips() {
    const accs = accessories().filter((a) => S.view === HOME || a.room === S.view);
    const sw = accs.filter((a) => a.kind === "switch" && !a.cover && a.online);
    const onCount = sw.filter((a) => a.on).length;
    const sensors = accs.filter((a) => a.kind === "sensor");
    const motion = sensors.filter((a) => a.online && a.sensor.icon === "motion" && a.sensor.active);
    const offline = accs.filter((a) => !a.online);
    const lowBatt = sensors.filter((a) => a.sensor.alert);
    const chips = [];
    if (sw.length) chips.push(chip("on", "light", onCount ? "#ff9f0a" : null, "Lights & Switches", onCount ? `${onCount} On` : "All Off"));
    if (sensors.length) chips.push(chip("sensors", "motion", motion.length ? "#ff9f0a" : "#64d2ff", "Sensors",
      motion.length ? `Motion · ${esc(motion[0].room)}` : `${sensors.length} Sensor${sensors.length > 1 ? "s" : ""}`));
    if (lowBatt.length) chips.push(chip("sensors", "battery", "#ff453a", "Battery", `${lowBatt.length} Low`));
    if (offline.length) chips.push(chip("offline", "warn", "#ff453a", "No Response", `${offline.length} Accessor${offline.length > 1 ? "ies" : "y"}`));
    $("#chips").innerHTML = chips.join("");
    $("#chips").hidden = !chips.length;
  }
  function chip(action, icon, color, title, sub) {
    const bg = color ? `background:${color}26;color:${color}` : "background:var(--icon-off-bg);color:var(--icon-off)";
    return `<button class="chip" data-chip="${action}"><span class="chip-ico" style="${bg}"><svg viewBox="0 0 24 24" fill="currentColor">${P[icon]}</svg></span><span><b>${esc(title)}</b><span class="chip-sub">${sub}</span></span></button>`;
  }

  function tileHTML(a) {
    const cls = ["tile", a.on && a.online ? "on" : "", !a.online ? "offline" : "", S.busy.has(a.key) ? "busy" : ""].join(" ");
    const alert = a.kind === "sensor" && (a.sensor.alert || (a.sensor.active && a.sensor.icon === "motion"));
    const label = `${a.name}, ${statusText(a)}${a.kind === "switch" && a.online ? ". Press to turn " + (a.on ? "off" : "on") + ", long-press for details" : ""}`;
    const pressed = a.kind === "switch" ? ` aria-pressed="${!!(a.on && a.online)}"` : "";
    return `<button class="${cls}" data-key="${esc(a.key)}"${pressed} style="--accent:${ACCENT[a.icon] || ACCENT.switch}" aria-label="${esc(label)}">
      <span class="t-icon">${svg(a.icon)}</span>
      <span class="t-text"><span class="t-name">${esc(a.name)}</span><span class="t-status${alert ? " alert" : ""}">${esc(statusText(a))}</span></span>
    </button>`;
  }

  function renderContent() {
    const el = $("#content");
    if (!S.devices.length) {
      el.innerHTML = `<div class="empty">No accessories found on the iHost.</div>`;
      return;
    }
    const all = accessories();
    let html = "";
    if (S.view === HOME) {
      const favs = S.prefs.favorites.map((k) => all.find((a) => a.key === k)).filter(Boolean);
      if (favs.length) html += section("Favorites", favs, null);
      for (const r of rooms()) html += section(r, all.filter((a) => a.room === r), r);
    } else {
      html += section(S.view, all.filter((a) => a.room === S.view), S.view, true);
    }
    el.innerHTML = html;
  }
  function section(title, accs, room, hideTitle) {
    if (!accs.length) return "";
    // switches first, then sensors; keep device/channel order stable
    accs = [...accs].sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "switch" ? -1 : 1));
    const anyOn = accs.some((a) => a.kind === "switch" && !a.cover && a.on && a.online);
    const btn = room ? `<button class="link-btn" data-room-off="${esc(room)}" ${anyOn ? "" : "disabled"}>Turn Off</button>` : "";
    return `<section class="section"><div class="section-head">${hideTitle ? `<span class="meta">${accs.length} accessor${accs.length > 1 ? "ies" : "y"}</span>` : `<h2>${esc(title)}</h2>`}${btn}</div>
      <div class="grid">${accs.map(tileHTML).join("")}</div></section>`;
  }

  // ------------------------------------------------------------------ actions
  function stateFor(a, on) {
    const v = on ? "on" : "off";
    if (a.cover) return { percentage: { percentage: on ? 100 : 0 } };
    return a.ch != null ? { toggle: { [a.ch]: { toggleState: v } } } : { power: { powerState: v } };
  }
  function applyLocal(sn, patch) {
    const d = findDev(sn);
    if (!d) return;
    d.state = d.state || {};
    for (const [cap, val] of Object.entries(patch)) {
      if (cap === "toggle") {
        d.state.toggle = d.state.toggle || {};
        for (const [ch, v] of Object.entries(val)) d.state.toggle[ch] = { ...(d.state.toggle[ch] || {}), ...v };
      } else if (val && typeof val === "object") {
        d.state[cap] = { ...(d.state[cap] || {}), ...val };
      }
    }
  }

  async function setOn(a, on, quiet) {
    if (!a.online || a.kind !== "switch") return false;
    const prev = a.on;
    const patch = stateFor(a, on);
    applyLocal(a.sn, patch);
    S.busy.add(a.key);
    render();
    try {
      await api("PUT", hp(`/devices/${encodeURIComponent(a.sn)}`), { state: patch });
      return true;
    } catch (e) {
      applyLocal(a.sn, stateFor(a, prev));
      if (!quiet) handleError(e, `Couldn't turn ${on ? "on" : "off"} ${a.name}`);
      return false;
    } finally {
      S.busy.delete(a.key);
      render();
    }
  }

  async function turnOffMany(list, label) {
    list = list.filter((a) => a.kind === "switch" && !a.cover && a.online && a.on);
    if (!list.length) return;
    let fails = 0;
    const queue = [...list];
    const worker = async () => { while (queue.length) { if (!(await setOn(queue.shift(), false, true))) fails++; } };
    await Promise.all([worker(), worker(), worker()]);
    toast(fails ? `${fails} of ${list.length} didn't respond` : `${label} off`, !!fails);
  }

  async function renameAccessory(a, name) {
    name = name.trim();
    if (!name || name === a.name) return;
    try {
      if (a.ch != null && a.multi) {
        await api("PATCH", hp(`/devices/${encodeURIComponent(a.sn)}/tags`), { toggle: { [a.ch]: name } });
        const d = findDev(a.sn);
        d.tags = d.tags || {};
        d.tags.toggle = { ...(d.tags.toggle || {}), [a.ch]: name };
      } else {
        await renameDevice(a.sn, name, true);
        return;
      }
      toast("Name saved");
      render();
    } catch (e) { handleError(e, "Couldn't rename"); render(); }
  }
  async function renameDevice(sn, name, silentCheck) {
    name = name.trim();
    const d = findDev(sn);
    if (!d || !name || name === d.name) return;
    try {
      await api("PUT", hp(`/devices/${encodeURIComponent(sn)}`), { name });
      d.name = name;
      toast("Name saved");
    } catch (e) { handleError(e, "Couldn't rename"); }
    render();
  }
  async function setRoom(sn, room) {
    const d = findDev(sn);
    if (!d) return;
    const value = room === DEFAULT_ROOM ? null : room;
    try {
      await api("PATCH", hp(`/devices/${encodeURIComponent(sn)}/tags`), { room: value });
      d.tags = d.tags || {};
      if (value) d.tags.room = value; else delete d.tags.room;
      toast(`Moved to ${room}`);
    } catch (e) { handleError(e, "Couldn't change room"); }
    render();
  }
  async function renameRoom(oldName, newName) {
    newName = newName.trim();
    if (!newName || newName === oldName) return;
    const devs = S.devices.filter((d) => roomOf(d) === oldName);
    let fails = 0;
    for (const d of devs) {
      try {
        await api("PATCH", hp(`/devices/${encodeURIComponent(d.serial_number)}/tags`), { room: newName === DEFAULT_ROOM ? null : newName });
        d.tags = d.tags || {};
        if (newName === DEFAULT_ROOM) delete d.tags.room; else d.tags.room = newName;
      } catch { fails++; }
    }
    if (S.view === oldName) S.view = newName;
    toast(fails ? `${fails} accessories couldn't be updated` : "Room updated", !!fails);
    render();
  }
  async function savePrefs(patch) {
    Object.assign(S.prefs, patch);
    render();
    try { S.prefs = await api("PUT", hp("/prefs"), patch); } catch (e) { handleError(e, "Couldn't save"); }
  }
  function toggleFavorite(key) {
    const f = new Set(S.prefs.favorites);
    f.has(key) ? f.delete(key) : f.add(key);
    savePrefs({ favorites: [...f] });
  }

  // ------------------------------------------------------------------ sheets
  function openSheet(spec, opener) {
    const wasOpen = !!S.sheet;
    S.sheet = spec;
    if (!wasOpen) S.opener = opener || document.activeElement;
    const sheet = $("#sheet"), bd = $("#backdrop");
    sheet.hidden = false; bd.hidden = false;
    renderSheet(true);
    requestAnimationFrame(() => { sheet.classList.add("show"); bd.classList.add("show"); });
    if (!wasOpen) setTimeout(() => { const f = $(".close-btn", sheet); f && f.focus({ preventScroll: true }); }, 60);
  }
  function closeSheet() {
    if (!S.sheet) return;
    const active = document.activeElement;
    if (active && active.blur && $("#sheet").contains(active)) active.blur(); // commit pending renames
    S.sheet = null;
    const sheet = $("#sheet"), bd = $("#backdrop");
    sheet.classList.remove("show"); bd.classList.remove("show");
    setTimeout(() => { if (!S.sheet) { sheet.hidden = true; bd.hidden = true; $("#sheetInner").innerHTML = ""; } }, 320);
    if (S.opener && document.contains(S.opener)) S.opener.focus({ preventScroll: true });
    else { const t = S.opener && S.opener.dataset && S.opener.dataset.key && document.querySelector(`.tile[data-key="${CSS.escape(S.opener.dataset.key)}"]`); t && t.focus({ preventScroll: true }); }
  }

  function renderSheet(force) {
    const inner = $("#sheetInner");
    const act = document.activeElement;
    // never re-render under the user's cursor while they type
    if (!force && act && inner.contains(act) && act.tagName === "INPUT" && act.type !== "checkbox") return;
    const scroll = inner.scrollTop;
    const sp = S.sheet;
    let html = "";
    if (sp.type === "acc") html = accSheet(sp);
    else if (sp.type === "settings") html = settingsSheet(sp);
    else if (sp.type === "list") html = listSheet(sp);
    else if (sp.type === "homes") html = homesSheet();
    if (html == null) { closeSheet(); return; }
    inner.innerHTML = html;
    inner.scrollTop = scroll;
  }

  const closeBtn = `<button class="close-btn" data-act="close" aria-label="Close"><svg viewBox="0 0 12 12"><path d="M1 1l10 10M11 1 1 11"/></svg></button>`;
  const chev = `<svg class="chev" viewBox="0 0 8 14"><path d="M1 1l6 6-6 6"/></svg>`;
  const checkMark = `<svg class="check" viewBox="0 0 18 18"><path d="M3 9.5l4 4 8-9"/></svg>`;
  const iosSwitch = (attrs, checked, disabled, label) =>
    `<label class="ios-switch"><input type="checkbox" role="switch" ${attrs} ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} aria-label="${esc(label)}"><span class="track"></span><span class="knob"></span></label>`;

  function head(icon, on, title, sub, accent, back) {
    return `<div class="sheet-head">${back ? `<button class="link-btn" data-act="${back}" style="font-size:17px">‹ Back</button>` : ""}
      ${icon ? `<span class="t-icon ${on ? "on" : ""}" style="--accent:${accent || ACCENT.switch}">${svg(icon)}</span>` : ""}
      <div class="titles"><h3 id="sheetTitle">${esc(title)}</h3>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div>${closeBtn}</div>`;
  }

  function signal(rssi) {
    if (typeof rssi !== "number") return null;
    const q = rssi >= -60 ? "Excellent" : rssi >= -70 ? "Good" : rssi >= -80 ? "Fair" : "Weak";
    return `${q} (${rssi} dBm)`;
  }

  function accSheet(sp) {
    const a = findAcc(sp.key);
    if (!a) return null;
    const d = a.device;
    const st = d.state || {};
    if (sp.sub === "room") return roomPicker(a);
    const accent = ACCENT[a.icon] || ACCENT.switch;
    const sub = a.multi ? `${a.room} · ${d.name}` : a.room;
    let h = head(a.icon, a.on && a.online, a.name, sub, accent);

    if (a.kind === "switch") {
      h += `<div class="big-switch-wrap">
        <button class="big-switch ${a.on && a.online ? "on" : ""}" data-act="toggle" style="--accent:${accent}" ${a.online ? "" : "disabled"}
          role="switch" aria-checked="${a.on}" aria-label="${esc(a.name)}"><span class="fill"></span><span class="glyph">${svg(a.icon)}</span></button>
        <div class="big-state">${a.online ? statusText(a) : "No Response"}</div></div>`;
    } else {
      const rows = [];
      if (st.motion) rows.push(["Motion", st.motion.motion ? "Detected" : "Not Detected"]);
      if (st.contact) rows.push(["Contact", st.contact.contact ? "Closed" : "Open"]);
      if (st.temperature) rows.push(["Temperature", `${st.temperature.temperature}°`]);
      if (st.humidity) rows.push(["Humidity", `${st.humidity.humidity}%`]);
      if (st["illumination-level"]) rows.push(["Light Level", String(Object.values(st["illumination-level"])[0])]);
      if (a.sensor.battery != null) rows.push(["Battery", `${a.sensor.battery}%`]);
      h += `<div class="group" style="margin-top:6px">${rows.map(([k, v]) => `<div class="row"><span class="lbl">${k}</span><span class="val">${esc(v)}</span></div>`).join("") ||
        `<div class="row"><span class="lbl">Status</span><span class="val">${esc(statusText(a))}</span></div>`}</div>`;
    }

    // name + room + favourites
    h += `<div class="group-label">${a.multi ? "Switch" : "Accessory"}</div><div class="group">
      <label class="row"><span class="lbl">Name</span><input class="inline" data-rename="acc" value="${esc(a.name)}" maxlength="64" enterkeyhint="done" aria-label="Name"></label>
      <button class="row tap" data-act="pick-room"><span class="lbl">Room</span><span class="val">${esc(a.room)}</span>${chev}</button>
      <div class="row"><span class="lbl">Include in Favorites</span>${iosSwitch('data-act="fav"', S.prefs.favorites.includes(a.key), false, "Include in Favorites")}</div>
    </div>`;
    if (a.multi) h += `<div class="group-foot">Room applies to all ${channelsOf(d).length} switches on this device.</div>`;

    if (a.kind === "switch" && !a.cover) {
      h += `<div class="group-label">Display As</div><div class="segmented" role="group" aria-label="Display as">${DISPLAY_AS.map(([k, l]) =>
        `<button data-icon="${k}" aria-pressed="${a.icon === k}">${svg(k)}${l}</button>`).join("")}</div>`;
    }

    if (a.multi) {
      const siblings = accessories().filter((x) => x.sn === a.sn);
      h += `<div class="group-label">Switches on This Device</div><div class="group">${siblings.map((x) => `
        <div class="row ${x.key === a.key ? "current" : ""}">
          <span class="mini-ico ${x.on && x.online ? "on" : ""}" style="--accent:${ACCENT[x.icon]}">${svg(x.icon)}</span>
          <input class="inline" style="text-align:left;color:var(--ink)" data-rename-ch="${esc(x.key)}" value="${esc(x.name)}" maxlength="64" enterkeyhint="done" aria-label="Name of switch ${esc(x.ch)}">
          ${iosSwitch(`data-sib="${esc(x.key)}"`, x.on, !x.online, x.name)}
        </div>`).join("")}</div>
        <div class="group-foot">Tap a name to rename that switch.</div>`;
    }

    // device info
    const rssi = st.rssi && st.rssi.rssi;
    const info = [
      a.multi ? null : null,
      ["Manufacturer", d.manufacturer], ["Model", d.model], ["Firmware", d.firmware_version],
      ["Connection", (d.protocol || d.protocal || "").toString().replace(/^./, (c) => c.toUpperCase()) || null],
      ["Signal", signal(rssi)], ["Status", a.online ? "Online" : "No Response"],
    ].filter((r) => r && r[1]);
    h += `<div class="group-label">Device</div><div class="group">
      ${a.multi ? `<label class="row"><span class="lbl">Device Name</span><input class="inline" data-rename="dev" value="${esc(d.name)}" maxlength="64" enterkeyhint="done" aria-label="Device name"></label>` : ""}
      ${info.map(([k, v]) => `<div class="row"><span class="lbl">${k}</span><span class="val">${esc(v)}</span></div>`).join("")}
      <button class="row tap" data-act="copy-sn" title="Copy serial number"><span class="lbl">Serial Number</span><span class="val mono">${esc(d.serial_number)}</span></button>
    </div>`;
    return h;
  }

  function roomPicker(a) {
    const list = rooms();
    if (!list.includes(a.room)) list.push(a.room);
    let h = head(null, false, "Room", a.name, null, "back");
    h += `<div class="group" style="margin-top:6px">${list.map((r) => `<button class="row tap" data-set-room="${esc(r)}"><span class="lbl">${esc(r)}</span>${r === a.room ? checkMark : ""}</button>`).join("")}</div>
      <div class="group-label">New Room</div>
      <div class="group"><label class="row"><input class="inline" style="text-align:left;color:var(--ink)" id="newRoom" placeholder="Room name" maxlength="40" enterkeyhint="done" aria-label="New room name"></label></div>
      <div style="margin-top:14px"><button class="btn" data-act="add-room">Move to New Room</button></div>`;
    return h;
  }

  function listSheet(sp) {
    const all = accessories().filter((a) => S.view === HOME || a.room === S.view);
    let list, title, empty;
    if (sp.which === "on") { list = all.filter((a) => a.kind === "switch" && !a.cover && a.online && a.on); title = "On Now"; empty = "Everything is off."; }
    else if (sp.which === "sensors") { list = all.filter((a) => a.kind === "sensor"); title = "Sensors"; empty = "No sensors."; }
    else { list = all.filter((a) => !a.online); title = "No Response"; empty = "All accessories are responding."; }
    let h = head(null, false, title, S.view === HOME ? homeName() : S.view);
    if (!list.length) return h + `<div class="empty">${empty}</div>`;
    h += `<div class="group">${list.map((a) => `<div class="row">
      <span class="mini-ico ${a.on && a.online ? "on" : ""}" style="--accent:${ACCENT[a.icon]}">${svg(a.icon)}</span>
      <button class="lbl" style="text-align:left" data-open="${esc(a.key)}">${esc(a.name)}<div style="font-size:13px;color:var(--ink-2)">${esc(a.room)} · ${esc(statusText(a))}</div></button>
      ${a.kind === "switch" ? iosSwitch(`data-sib="${esc(a.key)}"`, a.on, !a.online, a.name) : ""}</div>`).join("")}</div>`;
    if (sp.which === "on") h += `<div style="margin-top:16px"><button class="btn" data-act="all-off">Turn Off All ${list.length}</button></div>`;
    return h;
  }

  function settingsSheet(sp) {
    const s = S.status || {};
    const b = s.bridge || {};
    const sec = S.security || {};
    const remoteSession = !!S.session.remote;
    if (sp.sub === "room") {
      const n = S.devices.filter((d) => roomOf(d) === sp.room).length;
      let h = head(null, false, sp.room, `${n} device${n === 1 ? "" : "s"}`, null, "back");
      h += `<div class="group" style="margin-top:6px"><label class="row"><span class="lbl">Name</span><input class="inline" id="roomName" value="${esc(sp.room)}" maxlength="40" enterkeyhint="done" aria-label="Room name"></label></div>
        <div style="margin-top:14px"><button class="btn" data-act="save-room">Save</button></div>`;
      if (sp.room !== DEFAULT_ROOM) h += `<div style="margin-top:8px"><button class="btn danger" data-act="remove-room">Remove Room</button></div><div class="group-foot" style="text-align:center">Accessories move to ${DEFAULT_ROOM}.</div>`;
      return h;
    }
    if (sp.sub === "password") {
      let h = head(null, false, sec.has_password ? "Change Password" : "Set Password", "For signing in from outside your home", null, "back");
      h += `<form data-form="password" style="margin-top:6px" autocomplete="on">
        <input type="text" name="username" value="ihost-home" autocomplete="username" hidden>
        ${sec.has_password ? `<input class="field" id="pwCurrent" type="password" placeholder="${sec.weak_password ? "Current PIN" : "Current password"}" autocomplete="current-password" aria-label="Current password">` : ""}
        <input class="field" id="pwNew" type="password" placeholder="New password (at least 8 characters)" autocomplete="new-password" aria-label="New password">
        <input class="field" id="pwNew2" type="password" placeholder="Repeat new password" autocomplete="new-password" aria-label="Repeat new password">
        <button class="btn" type="submit">Save Password</button></form>
        <div class="group-foot" style="margin-top:10px">Saving signs out every other phone and browser.</div>`;
      return h;
    }
    if (sp.sub === "remote") return remoteSheet(sec);
    if (sp.sub === "hub-remove") {
      let h = head(null, false, "Remove iHost", homeName(), null, "back");
      h += `<p class="group-foot" style="margin:8px 4px 16px;font-size:15px">This only removes the connection from this app. Devices, names and rooms stay on the iHost, and you can add it again any time.</p>
        <button class="btn" style="background:var(--red)" data-act="remove-hub-confirm">Remove ${esc(homeName())}</button>`;
      return h;
    }

    let h = head("home", false, "Home Settings", null, null);
    h += `<div class="group"><label class="row"><span class="lbl">Home Name</span><input class="inline" id="homeName" value="${esc(homeName())}" maxlength="40" enterkeyhint="done" aria-label="Home name"></label>
      <button class="row tap" data-act="homes"><span class="lbl">Homes</span><span class="val">${S.hubs.length}</span>${chev}</button></div>`;

    const rs = rooms();
    h += `<div class="group-label">Rooms</div><div class="group">${rs.map((r) => {
      const n = S.devices.filter((d) => roomOf(d) === r).length;
      return `<button class="row tap" data-edit-room="${esc(r)}"><span class="lbl">${esc(r)}</span><span class="val">${n}</span>${chev}</button>`;
    }).join("")}</div><div class="group-foot">Put accessories in rooms from each accessory's settings (long-press a tile).</div>`;

    h += `<div class="group-label">iHost</div><div class="group">
      <div class="row"><span class="lbl">Hub</span><span class="val">${esc(b.name || "iHost")}</span></div>
      <div class="row"><span class="lbl">IP Address</span><span class="val">${esc(b.ip || currentHub().host || "—")}</span></div>
      ${b.fw_version ? `<div class="row"><span class="lbl">Firmware</span><span class="val">${esc(b.fw_version)}</span></div>` : ""}
      ${b.mac ? `<div class="row"><span class="lbl">MAC</span><span class="val mono">${esc(b.mac)}</span></div>` : ""}
      <div class="row"><span class="lbl">Live Updates</span><span class="val" style="color:${S.live ? "var(--green)" : S.polling ? "var(--orange)" : "var(--red)"}">${S.live ? "Instant" : S.polling ? "Every 3 seconds" : "Disconnected"}</span></div>
      <div class="row"><span class="lbl">Accessories</span><span class="val">${S.devices.length} devices · ${accessories().length} tiles</span></div>
      ${remoteSession ? "" : `<button class="row tap action" data-act="repair"><span class="lbl">Change IP Address / Pair Again…</span></button>
      <button class="row tap" style="color:var(--red)" data-act="remove-hub"><span class="lbl">Remove This iHost…</span></button>`}
    </div>`;

    const rem = sec.remote || {};
    const remState = rem.enabled ? (rem.state === "running" ? "On" : rem.state === "error" ? "Problem" : "Starting…") : "Off";
    h += `<div class="group-label">Access</div><div class="group">
      <button class="row tap" data-act="remote"><span class="lbl">Internet Access</span><span class="val" style="${rem.enabled && rem.state === "running" ? "color:var(--green)" : ""}">${remState}</span>${chev}</button>
      <button class="row tap" data-act="password"><span class="lbl">Password</span><span class="val">${sec.weak_password ? "PIN (upgrade)" : sec.has_password ? "Change" : "Not Set"}</span>${chev}</button>
      <div class="row"><span class="lbl">Ask for Password at Home</span>${iosSwitch('data-act="lan-login"', sec.lan_login, !sec.has_password, "Ask for password at home")}</div>
      <button class="row tap action" data-act="signout-all"><span class="lbl">Sign Out All Other Devices</span></button>
      ${S.session.login_required ? `<button class="row tap" style="color:var(--red)" data-act="signout"><span class="lbl">Sign Out</span></button>` : ""}
    </div><div class="group-foot">At home, the app opens without a password unless you turn it on. From the internet, a password is always required.</div>`;
    return h;
  }

  function remoteSheet(sec) {
    const rem = sec.remote || {};
    const remoteSession = !!S.session.remote;
    const stateText = { off: "Off", downloading: "Downloading connector…", starting: "Connecting…", running: "Connected", error: "Problem" }[rem.state] || rem.state;
    let h = head(null, false, "Internet Access", "Control this home from anywhere", null, "back");
    h += `<div class="group" style="margin-top:6px">
      <div class="row"><span class="lbl">Internet Access</span>${iosSwitch('data-act="remote-toggle"', rem.enabled, !sec.has_password || sec.weak_password || remoteSession, "Internet access")}</div>
      ${rem.enabled ? `<div class="row"><span class="lbl">Status</span><span class="val" style="color:${rem.state === "running" ? "var(--green)" : rem.state === "error" ? "var(--red)" : "var(--ink-2)"}">${esc(stateText)}</span></div>` : ""}
    </div>`;
    if (!sec.has_password) h += `<div class="group-foot">Set a password first. Anyone opening the link must enter it.</div><div style="margin-top:12px"><button class="btn" data-act="password">Set Password</button></div>`;
    else if (sec.weak_password) h += `<div class="group-foot">Your current PIN is too short to protect the home on the internet. Set a password with at least 8 characters first.</div><div style="margin-top:12px"><button class="btn" data-act="password">Set New Password</button></div>`;
    else if (remoteSession) h += `<div class="group-foot">You're connected over the internet. Turn this on or off from home.</div>`;
    else h += `<div class="group-foot">Uses a secure Cloudflare tunnel. No router or port-forwarding changes needed. This computer must stay on.</div>`;
    if (rem.error && rem.enabled) h += `<div class="group-foot" style="color:var(--red)">${esc(rem.error)}</div>`;

    if (rem.url) {
      h += `<div class="group-label">Your Link</div><div class="group">
        <div class="row"><span class="lbl mono" style="font-size:14px;white-space:normal;word-break:break-all;user-select:all">${esc(rem.url)}</span></div>
        <button class="row tap action" data-act="copy-url"><span class="lbl">Copy Link</span></button></div>
        <div class="group-foot">${rem.mode === "token" ? "Your permanent address." : "Free link. It changes when this computer or the app restarts. For a permanent address use your own domain below."}</div>`;
    }

    const linkBase = rem.url || location.origin;
    h += `<div class="group-label">Sign-in Link</div><div class="group">
      ${S.newLink ? `<div class="row"><span class="lbl mono" style="font-size:13px;white-space:normal;word-break:break-all;user-select:all">${esc(linkBase + "/login?key=" + S.newLink)}</span></div>
        <button class="row tap action" data-act="copy-link"><span class="lbl">Copy Sign-in Link</span></button>` : ""}
      <button class="row tap action" data-act="make-link"><span class="lbl">${sec.has_link ? "Create New Sign-in Link" : "Create Sign-in Link"}</span></button>
      ${sec.has_link ? `<button class="row tap" style="color:var(--red)" data-act="revoke-link"><span class="lbl">Turn Off Sign-in Link</span></button>` : ""}
    </div><div class="group-foot">Opens the app without typing the password. Share only with family. Creating a new link turns off the old one.${S.newLink ? " The link is shown once, so copy it now." : ""}</div>`;

    if (!remoteSession) {
      const tokenMode = rem.mode === "token";
      h += `<div class="group-label">Address Type</div>
        <div class="segmented" role="group" aria-label="Address type">
          <button data-mode="quick" aria-pressed="${!tokenMode}">Free Link</button>
          <button data-mode="token" aria-pressed="${tokenMode}">Own Domain</button></div>`;
      if (tokenMode) {
        h += `<div class="group" style="margin-top:12px">
          <label class="row"><span class="lbl">Tunnel Token</span><input class="inline" id="tunToken" type="password" placeholder="${rem.has_token ? "saved - paste to replace" : "paste token"}" autocomplete="off" aria-label="Cloudflare tunnel token"></label>
          <label class="row"><span class="lbl">Address</span><input class="inline" id="tunUrl" value="${esc(rem.public_url || "")}" placeholder="home.example.com" autocomplete="off" aria-label="Public address"></label></div>
          <div style="margin-top:12px"><button class="btn secondary" data-act="save-tunnel">Save</button></div>
          <div class="group-foot">In Cloudflare Zero Trust: Networks → Tunnels → Create a tunnel → copy the token. Add a public hostname pointing to <b>http://localhost:${location.port || 80}</b>.</div>`;
      }
    }
    return h;
  }

  function homesSheet() {
    let h = head(null, false, "Homes", `${S.hubs.length} iHost${S.hubs.length === 1 ? "" : "s"}`);
    h += `<div class="group" style="margin-top:6px">${S.hubs.map((x) => `<button class="row tap" data-hub="${esc(x.id)}">
        <span class="lbl">${esc(x.name)}<div style="font-size:13px;color:var(--ink-2)">${esc(x.host)}</div></span>
        <span class="live-dot ${x.live ? "on" : ""}" title="${x.live ? "Connected" : "Not connected"}"></span>
        ${x.id === S.hub ? checkMark : ""}</button>`).join("")}</div>`;
    if (!S.session.remote) h += `<div style="margin-top:14px"><button class="btn secondary" data-act="add-hub">Add iHost…</button></div>
      <div class="group-foot" style="text-align:center">Add any iHost on this network by its IP address.</div>`;
    return h;
  }

  // ------------------------------------------------------------------ full-page states
  function pageMode() {
    S.loaded = false;
    if (S.sheet) closeSheet();
    $("#roomsNav").innerHTML = ""; $("#chips").hidden = true;
    if (es) { es.close(); es = null; }
  }

  let pairJob = null;
  function showAddHub(opts = {}) {
    pageMode();
    const cancel = S.hubs.length ? `<div style="margin-top:8px"><button class="btn secondary" id="cancelAdd">Cancel</button></div>` : "";
    if (S.session.remote) {
      $("#content").innerHTML = `<div class="center-card"><h2>Add iHost</h2><p>New iHosts can only be added from the home network.</p>${cancel}</div>`;
      if (cancel) $("#cancelAdd").onclick = boot;
      return;
    }
    $("#content").innerHTML = `<div class="center-card wide">
      <div class="big-ico"><img src="/icon.svg" width="64" height="64" alt=""></div>
      <h2>${opts.title || "Add iHost"}</h2>
      <p>The iHost and this computer must be on the same network.</p>
      <div class="group-label" style="text-align:left;margin-left:4px">Found on Your Network</div>
      <div class="group" id="scanList"><div class="row"><span class="spinner" style="margin:0;width:18px;height:18px"></span><span class="lbl" style="color:var(--ink-2)">Searching…</span></div></div>
      <div style="text-align:right;margin:6px 2px 0"><button class="link-btn" id="rescan">Search Again</button></div>
      <div class="group-label" style="text-align:left;margin-left:4px">Or Enter the IP Address</div>
      <input class="field" id="hostInput" value="${esc(opts.host || "")}" placeholder="192.168.1.50" autocomplete="off" spellcheck="false" inputmode="decimal" aria-label="iHost IP address">
      <input class="field" id="nameInput" value="${esc(opts.name || "")}" placeholder="Name, e.g. Villa · New Cairo" maxlength="40" autocomplete="off" aria-label="Home name">
      <button class="btn" id="pairBtn">Connect</button>${cancel}
      ${opts.msg ? `<p style="color:var(--red);margin:14px 0 0">${esc(opts.msg)}</p>` : ""}
    </div>`;
    $("#pairBtn").onclick = () => startPair($("#hostInput").value.trim(), $("#nameInput").value.trim());
    $("#hostInput").onkeydown = (e) => { if (e.key === "Enter") $("#pairBtn").click(); };
    $("#rescan").onclick = runScan;
    if (cancel) $("#cancelAdd").onclick = boot;
    runScan();
  }

  async function runScan() {
    const list = $("#scanList");
    if (!list) return;
    list.innerHTML = `<div class="row"><span class="spinner" style="margin:0;width:18px;height:18px"></span><span class="lbl" style="color:var(--ink-2)">Searching your network…</span></div>`;
    try {
      const { found, networks } = await api("POST", "/api/scan");
      if (!$("#scanList")) return;
      if (!found.length) {
        list.innerHTML = `<div class="row"><span class="lbl" style="color:var(--ink-2);white-space:normal">No iHost found on ${esc((networks || []).map((n) => n.replace(/\.\d+$/, ".x")).join(", ") || "this network")}. Enter its IP below.</span></div>`;
        return;
      }
      list.innerHTML = found.map((f) => `<button class="row tap" data-pick-ip="${esc(f.ip)}" data-pick-name="${esc(f.paired_as || "")}">
        <span class="mini-ico on" style="--accent:#ff9f0a">${svg("home")}</span>
        <span class="lbl">${esc(f.name)} · ${esc(f.ip)}<div style="font-size:13px;color:var(--ink-2)">${f.paired_as ? `Added as “${esc(f.paired_as)}” · tap to pair again` : `Firmware ${esc(f.fw || "?")}`}</div></span>${chev}</button>`).join("");
      list.querySelectorAll("[data-pick-ip]").forEach((b) => b.onclick = () => {
        $("#hostInput").value = b.dataset.pickIp;
        if (!$("#nameInput").value && b.dataset.pickName) $("#nameInput").value = b.dataset.pickName;
        $("#nameInput").focus();
      });
    } catch (e) {
      if (e instanceof AuthError) return showLogin();
      list.innerHTML = `<div class="row"><span class="lbl" style="color:var(--red)">${esc(e.message)}</span></div>`;
    }
  }

  async function startPair(host, name) {
    if (!host) { $("#hostInput").focus(); return; }
    let job;
    try { job = (await api("POST", "/api/pair", { host, name })).job; } catch (e) { return showAddHub({ host, name, msg: e.message }); }
    pairJob = job;
    $("#content").innerHTML = `<div class="center-card">
      <div class="spinner"></div><h2 id="pairTitle">Connecting to ${esc(host)}…</h2>
      <ol class="steps"><li>Open <a href="${esc(/^https?:/.test(host) ? host : "http://" + host)}" target="_blank" rel="noopener" style="color:var(--blue)">${esc(host)}</a> in a browser on this network.</li>
      <li>A popup asks to allow <b>iHost Home</b>. Click <b>Done</b>.</li><li>This page continues by itself.</li></ol>
      <p style="margin:0 0 14px">You have 5 minutes.</p><button class="btn secondary" id="cancelPair">Cancel</button></div>`;
    $("#cancelPair").onclick = async () => { pairJob = null; try { await api("DELETE", `/api/pair/${job}`); } catch { /* ignore */ } showAddHub({ host, name }); };
    while (pairJob === job) {
      await new Promise((r) => setTimeout(r, 2000));
      if (pairJob !== job) return;
      let st;
      try { st = await api("GET", `/api/pair/${job}`); } catch { continue; }
      if (st.state === "waiting" && $("#pairTitle")) $("#pairTitle").textContent = `Waiting for approval on ${st.bridge && st.bridge.name || "iHost"}`;
      if (st.state === "done") {
        pairJob = null;
        localStorage.setItem("ihhHub", st.hub.id);
        toast(`Connected to ${st.hub.name}`);
        return boot();
      }
      if (["error", "timeout"].includes(st.state)) { pairJob = null; return showAddHub({ host, name, msg: st.message }); }
      if (st.state === "cancelled") return;
    }
  }

  function showLogin(msg) {
    pageMode();
    $("#homeTitle").textContent = "Home";
    $("#content").innerHTML = `<div class="center-card">
      <div class="big-ico"><img src="/icon.svg" width="64" height="64" alt=""></div>
      <h2>Sign In</h2><p>${S.session.remote ? "You're connecting from outside the home." : "This home is protected."}</p>
      <form id="loginForm" autocomplete="on">
        <input type="text" name="username" value="ihost-home" autocomplete="username" hidden>
        <input class="field" id="pwLogin" type="password" autocomplete="current-password" placeholder="Password" aria-label="Password">
        <button class="btn" type="submit">Sign In</button></form>
      ${msg ? `<p style="color:var(--red);margin:14px 0 0">${esc(msg)}</p>` : ""}</div>`;
    $("#loginForm").onsubmit = async (e) => {
      e.preventDefault();
      try { await api("POST", "/api/login", { password: $("#pwLogin").value }); boot(); }
      catch (err) { showLogin(err.message); }
    };
    setTimeout(() => $("#pwLogin") && $("#pwLogin").focus(), 50);
  }

  function showGatewayError(msg) {
    pageMode();
    $("#content").innerHTML = `<div class="center-card"><div class="big-ico" style="display:grid;place-items:center;background:var(--group-2);color:var(--red)"><svg viewBox="0 0 24 24" width="34" height="34" fill="currentColor">${P.warn}</svg></div>
      <h2>Can't reach ${esc(homeName())}</h2><p>${esc(msg)}</p><button class="btn" id="retryBtn">Try Again</button>
      ${S.session.remote ? "" : `<div style="margin-top:8px"><button class="btn secondary" id="repairBtn">Change IP Address</button></div>`}
      ${S.hubs.length > 1 ? `<div style="margin-top:8px"><button class="btn secondary" id="otherHome">Switch Home</button></div>` : ""}</div>`;
    $("#retryBtn").onclick = boot;
    if ($("#repairBtn")) $("#repairBtn").onclick = () => showAddHub({ title: "Change IP Address", host: currentHub().host, name: currentHub().name });
    if ($("#otherHome")) $("#otherHome").onclick = (e) => openSheet({ type: "homes" }, e.currentTarget);
  }

  // ------------------------------------------------------------------ feedback
  let toastTimer;
  function toast(msg, isError) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.toggle("error", !!isError);
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), isError ? 3600 : 1800);
  }
  function handleError(e, prefix) {
    if (e instanceof AuthError) return showLogin();
    toast(`${prefix}: ${e.message}`, true);
  }
  function banner(msg) {
    const b = $("#banner");
    b.hidden = !msg;
    b.textContent = msg || "";
  }

  // ------------------------------------------------------------------ data loading + live events
  let refreshTimer;
  async function refresh() {
    try {
      const { devices } = await api("GET", hp("/devices"));
      S.devices = devices;
      banner(null);
      render();
    } catch (e) {
      if (e instanceof AuthError) return showLogin();
      banner("Can't reach iHost — showing last known state");
    }
  }
  const scheduleRefresh = () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 400); };

  let es, pollTimer = null, sseWatch = null;
  function startPolling() {
    if (es) { es.close(); es = null; }
    if (pollTimer) return;
    S.live = false; S.polling = true;
    pollTimer = setInterval(() => { if (S.loaded && !document.hidden) refresh(); }, 3000);
    if (S.loaded) render();
  }
  function stopPolling() {
    clearInterval(pollTimer); pollTimer = null; S.polling = false;
  }
  function connectEvents() {
    if (es) es.close();
    stopPolling();
    clearTimeout(sseWatch);
    let gotFirst = false;
    es = new EventSource(hp("/events"));
    // some proxies (e.g. free Cloudflare quick tunnels) never deliver event streams: fall back to polling
    sseWatch = setTimeout(() => { if (!gotFirst) startPolling(); }, 8000);
    es.addEventListener("gateway", (ev) => {
      gotFirst = true;
      const was = S.live;
      S.live = !!JSON.parse(ev.data).connected;
      if (S.live && !was) scheduleRefresh(); // resync after reconnect
      if (S.loaded) render();
    });
    es.addEventListener("ihost", (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      const type = msg.type || "";
      const data = msg.data || {};
      const sn = data.endpoint && data.endpoint.serial_number;
      if (type.endsWith("#updateDeviceState") && sn && findDev(sn)) {
        applyLocal(sn, data.payload || {});
        render();
      } else if (type.endsWith("#updateDeviceOnline") && sn && findDev(sn)) {
        const p = data.payload || {};
        if (typeof p.online === "boolean") { findDev(sn).online = p.online; render(); } else scheduleRefresh();
      } else if (type.startsWith("device#")) {
        scheduleRefresh();
      }
    });
    es.onerror = () => {
      S.live = false;
      if (es && es.readyState === EventSource.CLOSED) startPolling(); // server refused the stream
      else if (S.loaded) render();
    };
  }

  async function boot() {
    try { S.session = await api("GET", "/api/session"); } catch (e) { return showGatewayError(e.message); }
    const params = new URLSearchParams(location.search);
    if (params.get("link") === "invalid") { toast("That sign-in link isn't valid anymore", true); history.replaceState(null, "", "/"); }
    if (S.session.login_required && !S.session.authed) return showLogin();
    try {
      S.hubs = (await api("GET", "/api/hubs")).hubs;
    } catch (e) {
      if (e instanceof AuthError) return showLogin();
      return showGatewayError(e.message);
    }
    if (!S.hubs.length) return showAddHub();
    const saved = localStorage.getItem("ihhHub");
    S.hub = (S.hubs.find((h) => h.id === saved) || S.hubs[0]).id;
    localStorage.setItem("ihhHub", S.hub);
    try {
      [S.status, S.prefs, S.security] = await Promise.all([api("GET", hp("/status")), api("GET", hp("/prefs")), api("GET", "/api/security")]);
    } catch (e) {
      if (e instanceof AuthError) return showLogin();
      return showGatewayError(e.message);
    }
    try {
      S.devices = (await api("GET", hp("/devices"))).devices;
    } catch (e) {
      if (e instanceof AuthError) return showLogin();
      return showGatewayError(S.status.error || e.message);
    }
    S.loaded = true;
    render();
    connectEvents();
  }

  function switchHub(id) {
    if (id === S.hub) return closeSheet();
    localStorage.setItem("ihhHub", id);
    S.devices = []; S.view = HOME;
    closeSheet();
    boot();
  }

  async function reloadSecurity() {
    try { S.security = await api("GET", "/api/security"); } catch { /* keep last */ }
  }
  // keep the Internet Access sheet live while the tunnel is starting
  setInterval(async () => {
    if (S.sheet && S.sheet.type === "settings" && S.sheet.sub === "remote") {
      await reloadSecurity();
      renderSheet();
    }
  }, 2500);

  // ------------------------------------------------------------------ events: page
  let pressTimer = null, longPressAt = 0, pressStart = null;
  const justLongPressed = () => Date.now() - longPressAt < 900;
  const content = $("#content");

  content.addEventListener("pointerdown", (e) => {
    const tile = e.target.closest(".tile");
    if (!tile || e.button !== 0) return;
    pressStart = [e.clientX, e.clientY];
    tile.classList.add("pressing");
    pressTimer = setTimeout(() => {
      longPressAt = Date.now();
      tile.classList.remove("pressing");
      if (navigator.vibrate) navigator.vibrate(12);
      openSheet({ type: "acc", key: tile.dataset.key }, tile);
    }, 480);
  });
  const cancelPress = () => { clearTimeout(pressTimer); pressTimer = null; document.querySelectorAll(".tile.pressing").forEach((t) => t.classList.remove("pressing")); };
  content.addEventListener("pointermove", (e) => {
    if (pressStart && Math.hypot(e.clientX - pressStart[0], e.clientY - pressStart[1]) > 10) cancelPress();
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach((t) => content.addEventListener(t, cancelPress));
  content.addEventListener("contextmenu", (e) => {
    const tile = e.target.closest(".tile");
    if (!tile) return;
    e.preventDefault();
    cancelPress();
    if (!justLongPressed()) openSheet({ type: "acc", key: tile.dataset.key }, tile);
  });
  content.addEventListener("click", (e) => {
    const off = e.target.closest("[data-room-off]");
    if (off) {
      const r = off.dataset.roomOff;
      return turnOffMany(accessories().filter((a) => a.room === r), r);
    }
    const tile = e.target.closest(".tile");
    if (!tile) return;
    if (justLongPressed()) return;
    const a = findAcc(tile.dataset.key);
    if (!a) return;
    if (a.kind === "switch" && a.online) setOn(a, !a.on);
    else openSheet({ type: "acc", key: a.key }, tile);
  });
  content.addEventListener("keydown", (e) => {
    const tile = e.target.closest(".tile");
    if (tile && (e.key === "i" || (e.shiftKey && e.key === "Enter"))) { e.preventDefault(); openSheet({ type: "acc", key: tile.dataset.key }, tile); }
  });

  $("#roomsNav").addEventListener("click", (e) => {
    const b = e.target.closest("[data-room]");
    if (!b) return;
    S.view = b.dataset.room;
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  $("#chips").addEventListener("click", (e) => {
    const c = e.target.closest("[data-chip]");
    if (c) openSheet({ type: "list", which: c.dataset.chip }, c);
  });
  $("#settingsBtn").addEventListener("click", async (e) => {
    if (!S.loaded) return;
    const btn = e.currentTarget;
    await reloadSecurity();
    openSheet({ type: "settings" }, btn);
  });
  $("#homeTitle").addEventListener("click", async (e) => {
    if (!S.loaded) return;
    const btn = e.currentTarget;
    try { S.hubs = (await api("GET", "/api/hubs")).hubs; } catch { /* keep last */ }
    openSheet({ type: "homes" }, btn);
  });
  $("#backdrop").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.sheet) closeSheet(); });
  window.addEventListener("scroll", () => $("#topbar").classList.toggle("scrolled", window.scrollY > 8), { passive: true });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && S.loaded) refresh(); });
  setInterval(() => { if (S.loaded && !document.hidden) refresh(); }, 60000);

  // ------------------------------------------------------------------ events: sheet
  const sheetEl = $("#sheet");
  sheetEl.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-act],[data-icon],[data-set-room],[data-edit-room],[data-open],[data-hub],[data-mode]");
    if (!t) return;
    const sp = S.sheet;
    if (!sp) return;
    const a = sp.key ? findAcc(sp.key) : null;
    if (t.dataset.open) return openSheet({ type: "acc", key: t.dataset.open });
    if (t.dataset.hub) return switchHub(t.dataset.hub);
    if (t.dataset.mode) {
      try { S.security.remote = await api("PUT", "/api/remote", { mode: t.dataset.mode }); } catch (err) { handleError(err, "Couldn't change"); }
      return renderSheet();
    }
    if (t.matches('input[type="checkbox"]')) return; // switches are handled on "change"
    if (t.dataset.icon && a) {
      const icons = { ...S.prefs.icons, [a.key]: t.dataset.icon };
      return savePrefs({ icons });
    }
    if (t.dataset.setRoom && a) { S.sheet = { type: "acc", key: a.key }; return setRoom(a.sn, t.dataset.setRoom); }
    if (t.dataset.editRoom) return openSheet({ type: "settings", sub: "room", room: t.dataset.editRoom });
    switch (t.dataset.act) {
      case "close": return closeSheet();
      case "back": S.newLink = null; return openSheet(sp.type === "acc" ? { type: "acc", key: sp.key } : { type: "settings" });
      case "toggle": if (a) setOn(a, !a.on); return;
      case "pick-room": return openSheet({ ...sp, sub: "room" });
      case "add-room": {
        const name = ($("#newRoom").value || "").trim();
        if (!name) return $("#newRoom").focus();
        S.sheet = { type: "acc", key: a.key };
        return setRoom(a.sn, name);
      }
      case "copy-sn":
        try { await navigator.clipboard.writeText(a.sn); toast("Serial number copied"); } catch { toast(a.sn); }
        return;
      case "all-off": return turnOffMany(accessories().filter((x) => S.view === HOME || x.room === S.view), "Everything");
      case "repair": return showAddHub({ title: "Change IP Address", host: currentHub().host, name: currentHub().name });
      case "remove-hub": return openSheet({ type: "settings", sub: "hub-remove" });
      case "remove-hub-confirm": {
        try {
          await api("DELETE", hp(""));
          localStorage.removeItem("ihhHub");
          toast("iHost removed");
          closeSheet();
          return boot();
        } catch (err) { return handleError(err, "Couldn't remove"); }
      }
      case "homes": return openSheet({ type: "homes" });
      case "add-hub": return showAddHub();
      case "remote": await reloadSecurity(); return openSheet({ type: "settings", sub: "remote" });
      case "password": return openSheet({ type: "settings", sub: "password" });
      case "copy-url": return copyText(S.security.remote.url, "Link copied");
      case "copy-link": return copyText(`${S.security.remote.url || location.origin}/login?key=${S.newLink}`, "Sign-in link copied");
      case "make-link":
        try { S.newLink = (await api("POST", "/api/security/link")).key; await reloadSecurity(); renderSheet(); }
        catch (err) { handleError(err, "Couldn't create link"); }
        return;
      case "revoke-link":
        try { await api("DELETE", "/api/security/link"); S.newLink = null; await reloadSecurity(); renderSheet(); toast("Sign-in link turned off"); }
        catch (err) { handleError(err, "Couldn't turn off"); }
        return;
      case "signout-all":
        try { await api("POST", "/api/security/signout-all"); toast("Other devices signed out"); } catch (err) { handleError(err, "Couldn't sign out"); }
        return;
      case "signout":
        try { await api("POST", "/api/logout"); } catch { /* ignore */ }
        closeSheet();
        return boot();
      case "save-tunnel": {
        const body = { public_url: $("#tunUrl").value.trim() };
        if ($("#tunToken").value.trim()) body.tunnel_token = $("#tunToken").value.trim();
        try { S.security.remote = await api("PUT", "/api/remote", body); toast("Saved"); renderSheet(); }
        catch (err) { handleError(err, "Couldn't save"); }
        return;
      }
      case "save-room": {
        const nn = $("#roomName").value;
        const old = sp.room;
        await renameRoom(old, nn);
        return openSheet({ type: "settings" });
      }
      case "remove-room": {
        await renameRoom(sp.room, DEFAULT_ROOM);
        return openSheet({ type: "settings" });
      }
    }
  });
  sheetEl.addEventListener("change", (e) => {
    const t = e.target;
    if (t.matches('[data-act="fav"]') && S.sheet && S.sheet.key) return toggleFavorite(S.sheet.key);
    if (t.dataset.sib) {
      const x = findAcc(t.dataset.sib);
      if (x) setOn(x, t.checked);
      return;
    }
    if (t.dataset.rename === "acc") { const a = findAcc(S.sheet.key); if (a) renameAccessory(a, t.value); return; }
    if (t.dataset.rename === "dev") { const a = findAcc(S.sheet.key); if (a) renameDevice(a.sn, t.value); return; }
    if (t.dataset.renameCh) { const x = findAcc(t.dataset.renameCh); if (x) renameAccessory(x, t.value); return; }
    if (t.id === "homeName" && t.value.trim()) return renameHub(t.value.trim());
    if (t.matches('[data-act="lan-login"]')) return setSecurity({ lan_login: t.checked }, t);
    if (t.matches('[data-act="remote-toggle"]')) return setRemote(t.checked, t);
  });
  sheetEl.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t.tagName !== "INPUT" || t.type === "checkbox") return;
    if (e.key === "Enter") {
      e.preventDefault();
      if (t.id === "newRoom") return $('[data-act="add-room"]').click();
      if (t.id === "tunToken" || t.id === "tunUrl") return $('[data-act="save-tunnel"]').click();
      if (t.form) return t.form.requestSubmit();
      if (t.id === "roomName") return $('[data-act="save-room"]').click();
      t.blur();
    } else if (e.key === "Escape") {
      t.value = t.defaultValue;
      t.blur();
      e.stopPropagation();
    }
  });
  sheetEl.addEventListener("submit", async (e) => {
    if (e.target.dataset.form !== "password") return;
    e.preventDefault();
    const cur = $("#pwCurrent"), n1 = $("#pwNew").value, n2 = $("#pwNew2").value;
    if (n1.length < 8) return toast("Use at least 8 characters", true);
    if (n1 !== n2) return toast("The new passwords don't match", true);
    try {
      await api("POST", "/api/security/password", { current: cur ? cur.value : "", new: n1 });
      await reloadSecurity();
      toast("Password saved");
      openSheet({ type: "settings" });
    } catch (err) { handleError(err, "Couldn't save"); }
  });

  async function copyText(text, msg) {
    try { await navigator.clipboard.writeText(text); toast(msg); }
    catch { window.prompt("Copy this:", text); }
  }
  async function renameHub(name) {
    try {
      const h = await api("PATCH", hp(""), { name });
      const cur = currentHub(); cur.name = h.name;
      toast("Name saved"); render();
    } catch (e) { handleError(e, "Couldn't rename"); }
  }
  async function setSecurity(patch, el) {
    try { await api("PUT", "/api/security/settings", patch); await reloadSecurity(); renderSheet(); }
    catch (e) { if (el) el.checked = !el.checked; handleError(e, "Couldn't change"); }
  }
  async function setRemote(on, el) {
    try { S.security.remote = await api("PUT", "/api/remote", { enabled: on }); renderSheet(); toast(on ? "Starting internet access…" : "Internet access off"); }
    catch (e) { if (el) el.checked = !on; handleError(e, "Couldn't change"); }
  }

  // after an input loses focus, catch up on any live updates we skipped while typing
  sheetEl.addEventListener("focusout", () => setTimeout(() => { if (S.sheet) renderSheet(); }, 50));

  // drag the sheet down to dismiss (phones)
  let drag = null;
  sheetEl.addEventListener("pointerdown", (e) => {
    if (window.innerWidth >= 720 || !e.target.closest(".sheet-grabber, .sheet-head") || e.target.closest("button,input")) return;
    drag = { y: e.clientY, dy: 0 };
    sheetEl.style.transition = "none";
    sheetEl.setPointerCapture(e.pointerId);
  });
  sheetEl.addEventListener("pointermove", (e) => {
    if (!drag) return;
    drag.dy = Math.max(0, e.clientY - drag.y);
    sheetEl.style.transform = `translateY(${drag.dy}px)`;
  });
  const endDrag = () => {
    if (!drag) return;
    sheetEl.style.transition = "";
    sheetEl.style.transform = "";
    if (drag.dy > 110) closeSheet();
    drag = null;
  };
  sheetEl.addEventListener("pointerup", endDrag);
  sheetEl.addEventListener("pointercancel", endDrag);

  boot();
})();
