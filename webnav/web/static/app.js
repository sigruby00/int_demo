"use strict";
const $ = (s) => document.querySelector(s);
const api = (url, opt) => fetch(url, opt).then((r) => r.json());
const post = (url, body) =>
  api(url, { method: "POST", headers: { "Content-Type": "application/json" },
             body: JSON.stringify(body || {}) });

// ---- virtual joystick ----------------------------------------------------
const MAX_LINEAR = 0.4, MAX_ANGULAR = 1.2;
const pad = $("#joystick"), stick = $("#stick");
let dragging = false, sendTimer = null, cur = { lin: 0, ang: 0 };

function setStick(dx, dy) {
  const R = pad.clientWidth / 2 - stick.clientWidth / 2;
  const d = Math.hypot(dx, dy);
  if (d > R) { dx *= R / d; dy *= R / d; }
  stick.style.left = `calc(50% + ${dx}px)`;
  stick.style.top = `calc(50% + ${dy}px)`;
  cur.lin = -(dy / R) * MAX_LINEAR;      // up = forward
  cur.ang = -(dx / R) * MAX_ANGULAR;     // left = CCW
}
function resetStick() {
  stick.style.left = "50%"; stick.style.top = "50%";
  cur.lin = 0; cur.ang = 0;
}
function padPoint(e) {
  const r = pad.getBoundingClientRect();
  const p = e.touches ? e.touches[0] : e;
  return { dx: p.clientX - (r.left + r.width / 2),
           dy: p.clientY - (r.top + r.height / 2) };
}
function startDrag(e) { dragging = true; move(e); e.preventDefault(); }
function move(e) { if (!dragging) return; const { dx, dy } = padPoint(e); setStick(dx, dy); e.preventDefault(); }
function endDrag() { if (!dragging) return; dragging = false; resetStick(); post("/api/stop"); }

pad.addEventListener("mousedown", startDrag);
window.addEventListener("mousemove", move);
window.addEventListener("mouseup", endDrag);
pad.addEventListener("touchstart", startDrag, { passive: false });
pad.addEventListener("touchmove", move, { passive: false });
pad.addEventListener("touchend", endDrag);

// ---- keyboard drive (WASD, Space = stop) ---------------------------------
const keys = {};
let keyDriving = false;
function keyVel() {
  let lin = 0, ang = 0;
  if (keys.w) lin += MAX_LINEAR;
  if (keys.s) lin -= MAX_LINEAR;
  if (keys.a) ang += MAX_ANGULAR;      // left = CCW
  if (keys.d) ang -= MAX_ANGULAR;
  return { lin, ang };
}
window.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;  // don't hijack typing
  const k = e.key.toLowerCase();
  if (k === " ") { keys.w = keys.a = keys.s = keys.d = false; keyDriving = false;
                   resetStick(); post("/api/stop"); e.preventDefault(); return; }
  if (!"wasd".includes(k)) return;
  keys[k] = true; keyDriving = true;
  const v = keyVel(); cur.lin = v.lin; cur.ang = v.ang;
  e.preventDefault();
});
window.addEventListener("keyup", (e) => {
  const k = e.key.toLowerCase();
  if (!"wasd".includes(k)) return;
  keys[k] = false;
  const v = keyVel(); cur.lin = v.lin; cur.ang = v.ang;
  if (!(keys.w || keys.a || keys.s || keys.d)) {
    keyDriving = false; cur.lin = 0; cur.ang = 0; post("/api/stop");
  }
});

// stream velocity while dragging or key-driving (10 Hz)
sendTimer = setInterval(() => {
  if ((dragging || keyDriving) && (cur.lin || cur.ang))
    post("/api/drive", { linear: cur.lin, angular: cur.ang });
}, 100);

// Safety: if the window loses focus / is hidden / closed while a key is held,
// the keyup never fires and the robot would keep driving forever. Force-stop on
// any of these so a lost keyup can't latch a spin.
function forceStop() {
  keys.w = keys.a = keys.s = keys.d = false;
  keyDriving = false; cur.lin = 0; cur.ang = 0;
  resetStick(); post("/api/stop");
}
window.addEventListener("blur", forceStop);
window.addEventListener("pagehide", forceStop);
document.addEventListener("visibilitychange", () => { if (document.hidden) forceStop(); });

$("#estop").onclick = () => { resetStick(); post("/api/stop"); };

// ---- teleop record & replay ---------------------------------------------
$("#recStart").onclick = () =>
  post("/api/record/start").then((r) => { if (!r.ok) alert(r.detail); });
$("#recStop").onclick = () => {
  const name = $("#recName").value.trim();
  if (!name) { alert("enter a trajectory name"); return; }
  post("/api/record/stop", { name }).then((r) => {
    if (r.ok) { $("#recName").value = ""; flash($("#recStop"), "Saved"); refreshState(); }
    else alert(typeof r.detail === "string" ? r.detail : "failed");
  });
};
$("#replayGo").onclick = () => {
  const name = $("#trajSelect").value;
  if (!name) { alert("no trajectory selected"); return; }
  post("/api/replay/start", { name, loop: $("#replayLoop").checked })
    .then((r) => { if (!r.ok) alert(r.detail); });
};
$("#replayStop").onclick = () => post("/api/replay/stop");

// ---- robot id ------------------------------------------------------------
$("#ridApply").onclick = () => {
  const id = parseInt($("#ridInput").value, 10);
  if (!id) return;
  post("/api/robot_id", { id }).then((r) => {
    if (r.ok) $("#ridLabel").textContent = "#" + r.robot_id;
    else alert(r.error || "failed");
  });
};

// ---- maps ----------------------------------------------------------------
$("#applyMap").onclick = () => {
  const name = $("#mapSelect").value;
  if (name) post("/api/map", { name }).then(() => refreshState());
};

// ---- waypoints -----------------------------------------------------------
let allWaypoints = {};
let route = [];

function populateRouteAdd() {
  const sel = $("#routeAdd"); const prev = sel.value;
  sel.innerHTML = "";
  Object.keys(allWaypoints).forEach((n) => {
    const o = document.createElement("option"); o.value = o.textContent = n; sel.append(o);
  });
  if (prev) sel.value = prev;
}

function renderRoute() {
  const ol = $("#routeList"); ol.innerHTML = "";
  route.forEach((name, i) => {
    const li = document.createElement("li");
    const exists = allWaypoints[name];
    const cur = (window._missionIdx === i);
    li.className = cur ? "cur" : (exists ? "" : "missing");
    const lbl = document.createElement("span");
    lbl.innerHTML = `<b>${name}</b>` + (exists ? "" : " <span class='muted'>(deleted)</span>");
    const ctrl = document.createElement("div"); ctrl.className = "rctrl";
    const up = document.createElement("button"); up.textContent = "↑"; up.className = "mini";
    up.onclick = () => { if (i > 0) { [route[i-1], route[i]] = [route[i], route[i-1]]; renderRoute(); } };
    const dn = document.createElement("button"); dn.textContent = "↓"; dn.className = "mini";
    dn.onclick = () => { if (i < route.length-1) { [route[i+1], route[i]] = [route[i], route[i+1]]; renderRoute(); } };
    const rm = document.createElement("button"); rm.textContent = "✕"; rm.className = "mini-del";
    rm.onclick = () => { route.splice(i, 1); renderRoute(); };
    ctrl.append(up, dn, rm); li.append(lbl, ctrl); ol.append(li);
  });
}

$("#routeAddBtn").onclick = () => { const n = $("#routeAdd").value; if (n) { route.push(n); renderRoute(); } };
$("#routeSave").onclick = () => post("/api/route", { route }).then(() => flash($("#routeSave"), "Saved"));
$("#missionStart").onclick = () => {
  if (!route.length) return;
  post("/api/mission/start", { route }).then((r) => { if (!r.ok) alert(r.error || "failed"); });
};
$("#missionStop").onclick = () => post("/api/mission/stop");

function flash(btn, txt) { const o = btn.textContent; btn.textContent = txt; setTimeout(() => btn.textContent = o, 900); }

function renderWaypoints(wps) {
  allWaypoints = wps; populateRouteAdd();
  const ul = $("#wpList"); ul.innerHTML = "";
  Object.entries(wps).forEach(([name, p]) => {
    const li = document.createElement("li");
    li.innerHTML =
      `<div><b>${name}</b> <span>(${p.x.toFixed(2)}, ${p.y.toFixed(2)})</span></div>`;
    const go = document.createElement("button");
    go.textContent = "Go"; go.onclick = () => post("/api/goto", { name });
    const del = document.createElement("button");
    del.textContent = "✕"; del.className = "mini-del";
    del.onclick = () => api(`/api/waypoints/${encodeURIComponent(name)}`,
                            { method: "DELETE" }).then((r) => renderWaypointsAll(r.waypoints));
    const box = document.createElement("div"); box.append(go, del); box.style.display = "flex"; box.style.gap = "6px";
    li.append(box); ul.append(li);
  });
}
$("#wpAdd").onclick = () => {
  const name = $("#wpName").value.trim();
  if (!name) return;
  post("/api/waypoints", { name }).then((r) => { if (r.ok) { renderWaypoints(r.waypoints); $("#wpName").value = ""; } });
};

function renderWaypointsAll(wps) {
  renderWaypoints(wps);
  if (window.mapSetWaypoints) window.mapSetWaypoints(wps);
}
// let map.js refresh the whole waypoint UI (list + route dropdown + map) after
// saving a waypoint from the map.
window.applyWaypoints = renderWaypointsAll;

// ---- BS list -------------------------------------------------------------
let lastRssi = {};
function renderBS(bs) {
  const tb = $("#bsTable tbody"); tb.innerHTML = "";
  bs.forEach((b) => {
    const tr = document.createElement("tr");
    const info = lastRssi[b.bssid.toLowerCase()];
    const rssi = info ? info.rssi : null;
    const ssid = info && info.ssid ? info.ssid : "—";
    if (info && info.connected) tr.className = "connected";
    const pct = rssi == null ? 0 : Math.max(0, Math.min(100, (rssi + 100) * 1.6));
    tr.innerHTML =
      `<td>${b.ap_id}</td><td>${ssid}</td><td>${b.bssid}</td>` +
      `<td>${rssi == null ? "—" : rssi + " dBm"}` +
      `<div class="rssi-bar" style="width:${pct}%"></div></td>`;
    const td = document.createElement("td");
    const del = document.createElement("button");
    del.textContent = "✕"; del.className = "mini-del";
    del.onclick = () => api(`/api/bs/${encodeURIComponent(b.bssid)}`,
                            { method: "DELETE" }).then((r) => renderBS(r.bs));
    td.append(del); tr.append(td); tb.append(tr);
  });
  renderHandoverSelect(bs);
}
function renderHandoverSelect(bs) {
  const sel = $("#hoSelect");
  const prev = sel.value;
  sel.innerHTML = "";
  bs.forEach((b) => {
    const o = document.createElement("option");
    o.value = b.bssid; o.textContent = `AP${b.ap_id}  ·  ${b.bssid}`;
    sel.append(o);
  });
  if (prev) sel.value = prev;
}
$("#hoGo").onclick = () => {
  const bssid = $("#hoSelect").value;
  if (!bssid) return;
  $("#hoGo").disabled = true; $("#hoGo").textContent = "…";
  post("/api/handover", { bssid }).then((r) => {
    $("#hoGo").disabled = false; $("#hoGo").textContent = "Handover";
    if (!r.ok) alert("handover incomplete (now on " + (r.bssid || "?") + ")");
  });
};
$("#hoAuto").onclick = () => post("/api/handover_auto");

$("#bsAdd").onclick = () => {
  const bssid = $("#bsMac").value.trim();
  const ap_id = $("#bsId").value.trim();
  if (!bssid) return;
  post("/api/bs", { bssid, ap_id: ap_id || undefined }).then((r) => {
    if (r.ok) { renderBS(r.bs); $("#bsMac").value = ""; $("#bsId").value = ""; }
    else alert(r.error || "failed");
  });
};

// ---- network interfaces --------------------------------------------------
function renderNet(data) {
  // eth-only network config gate
  const allowed = !!data.eth_config_allowed;
  $("#netcfgBody").hidden = !allowed;
  $("#netcfgLocked").hidden = allowed;
  const tb = $("#netTable tbody"); tb.innerHTML = "";
  (data.interfaces || []).forEach((n) => {
    const tr = document.createElement("tr");
    const down = !n.ip;
    if (n.ip) tr.className = "connected";
    const label = n.type === "wifi"
      ? (n.ssid ? `📶 ${n.ssid}` : (n.connection || "—"))
      : (n.connection || (n.state === "unavailable" ? "no cable" : "—"));
    const mode = n.method ? n.method : "—";
    tr.innerHTML =
      `<td><b>${n.name}</b></td><td>${label}</td>` +
      `<td>${n.ip || "—"}</td>` +
      `<td>${down ? '<span class="muted">'+ (n.state||'down') +'</span>' : mode}</td>`;
    tb.append(tr);
  });
}
function refreshNet() { api("/api/net").then(renderNet).catch(() => {}); }

// ---- network config (ethernet only) --------------------------------------
function ncMsg(t, ok) { const e = $("#ncMsg"); e.textContent = t; e.style.color = ok ? "var(--ok)" : "var(--danger)"; }
$("#ncScan").onclick = () => {
  const iface = $("#ncIface").value;
  ncMsg("scanning " + iface + "…", true);
  api(`/api/net/scan?iface=${iface}`).then((r) => {
    if (r.error) { ncMsg(r.error, false); return; }
    const sel = $("#ncSsid"); sel.innerHTML = "";
    (r.networks || []).forEach((n) => {
      const o = document.createElement("option");
      o.value = n.ssid;
      o.textContent = `${n.ssid}  (${n.signal}%${n.security ? " · " + n.security : ""})`;
      sel.append(o);
    });
    ncMsg(`${(r.networks || []).length} networks`, true);
  }).catch(() => ncMsg("scan failed", false));
};
$("#ncConnect").onclick = () => {
  const iface = $("#ncIface").value, ssid = $("#ncSsid").value, password = $("#ncPass").value;
  if (!ssid) { ncMsg("pick an SSID", false); return; }
  ncMsg(`connecting ${iface} → ${ssid}…`, true);
  post("/api/net/connect", { iface, ssid, password }).then((r) =>
    ncMsg(r.ok ? `connected: ${r.detail}` : r.error || r.detail, r.ok));
};
document.querySelectorAll('input[name="ncMethod"]').forEach((r) => {
  r.onchange = () => { $("#ncStatic").hidden = (document.querySelector('input[name="ncMethod"]:checked').value !== "manual"); };
});
$("#ncApplyIp").onclick = () => {
  const iface = $("#ncIface").value;
  const method = document.querySelector('input[name="ncMethod"]:checked').value;
  const body = { iface, method };
  if (method === "manual") { body.ip = $("#ncIp").value; body.gateway = $("#ncGw").value; body.dns = $("#ncDns").value; }
  ncMsg(`applying ${method} on ${iface}…`, true);
  post("/api/net/ipv4", body).then((r) => ncMsg(r.ok ? `applied: ${r.detail}` : r.error || r.detail, r.ok));
};

// ---- live state poll -----------------------------------------------------
function refreshState() {
  api("/api/state").then((s) => {
    const p = s.sensing.pose || s.state;
    $("#pose").textContent = `pose ${(p.x||0).toFixed(2)}, ${(p.y||0).toFixed(2)}, ${(p.yaw||0).toFixed(2)}`;
    $("#battery").textContent = "battery " + (s.state.battery == null ? "—" : s.state.battery + "%");
    const u = s.usb_joystick;
    const uc = $("#usb");
    uc.textContent = "USB joystick: " + (u.connected ? (u.name || "on") : "none");
    uc.className = "chip " + (u.connected ? "on" : "off");
    $("#curMap").textContent = s.current_map || "—";
    // mission status
    const m = s.mission || {};
    const ms = $("#missionStatus");
    ms.textContent = (m.running ? "▶ " : "") + (m.message || "idle");
    ms.className = "hint" + (m.running ? " running" : "");
    const newIdx = m.running ? m.index : -1;
    if (window._missionIdx !== newIdx) { window._missionIdx = newIdx; renderRoute(); }
    if (s.robot_id != null) {
      $("#ridLabel").textContent = "#" + s.robot_id;
      const ri = $("#ridInput");
      if (document.activeElement !== ri) ri.value = s.robot_id;   // don't fight typing
    }
    // robot pose -> map overlay
    if (window.mapSetRobot) window.mapSetRobot({ x: s.state.x, y: s.state.y, yaw: s.state.yaw });
    // map dropdown (auto-view first map once)
    const sel = $("#mapSelect");
    if (sel.dataset.n != String((s.maps || []).length)) {
      sel.innerHTML = ""; (s.maps || []).forEach((m) => {
        const o = document.createElement("option"); o.value = o.textContent = m; sel.append(o);
      });
      sel.dataset.n = String((s.maps || []).length);
      if (s.default_map && (s.maps || []).includes(s.default_map)) sel.value = s.default_map;
      if ((s.maps || []).length && window.mapLoad) window.mapLoad(sel.value);
    }
    // handover: current connection + roam mode
    const sen = s.sensing || {};
    $("#hoCurrent").textContent = sen.connected_ap_id
      ? `AP${sen.connected_ap_id} · ${sen.connected_bssid}`
      : (sen.connected_bssid || "—");
    const roam = s.roam || {};
    const rs = $("#roamStatus");
    const autoBtn = $("#hoAuto");
    if (roam.auto_roam) {
      rs.textContent = "🔄 Auto-roam: ON";
      rs.className = "roam auto";
      autoBtn.classList.add("active");
    } else {
      rs.textContent = "🔒 Locked to " + (roam.locked_bssid || "a BSSID");
      rs.className = "roam locked";
      autoBtn.classList.remove("active");
    }
    // record & replay status + trajectory dropdown
    const rec = s.recorder || {};
    const rst = $("#recStatus");
    if (rst) {
      if (rec.recording) {
        rst.textContent = `● Recording… ${rec.record_points} pts / ${rec.record_elapsed || 0}s`;
        rst.className = "roam locked";
      } else if (rec.replay && rec.replay.running) {
        const rp = rec.replay;
        rst.textContent = `▶ Replaying "${rp.name}" · lap ${rp.lap} (${rp.elapsed}/${rp.duration}s)${rp.loop ? " ⟳" : ""}`;
        rst.className = "roam auto";
      } else {
        rst.textContent = `idle · ${(rec.trajectories || []).length} saved`;
        rst.className = "roam";
      }
      const tsel = $("#trajSelect");
      if (tsel && tsel.dataset.n !== String((rec.trajectories || []).length)) {
        const curv = tsel.value;
        tsel.innerHTML = "";
        (rec.trajectories || []).forEach((n) => {
          const o = document.createElement("option"); o.value = o.textContent = n; tsel.append(o);
        });
        tsel.dataset.n = String((rec.trajectories || []).length);
        if (curv) tsel.value = curv;
      }
    }

    // sensing rssi cache -> BS table
    lastRssi = {};
    (s.sensing.connections || []).forEach((c) => {
      lastRssi[c.bssid.toLowerCase()] = { rssi: c.rssi, connected: c.connected, ssid: c.ssid };
    });
    api("/api/bs").then(renderBS);
  }).catch(() => {});
}

// init
api("/api/waypoints").then(renderWaypointsAll);
api("/api/route").then((r) => { route = r.route || []; renderRoute(); });
api("/api/bs").then(renderBS);
setInterval(refreshState, 700);
setInterval(refreshNet, 5000);
refreshState();
refreshNet();
