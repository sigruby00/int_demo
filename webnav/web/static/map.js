"use strict";
// Interactive SLAM-map viewer: shows the occupancy map, the live robot pose,
// saved waypoints, and lets the user click to set the current pose or send /
// save a waypoint (click a point, drag to aim the heading).
(function () {
  const canvas = document.getElementById("mapCanvas");
  const empty = document.getElementById("mapEmpty");
  const ctx = canvas.getContext("2d");
  const img = new Image();

  let meta = null;       // {resolution, origin:[ox,oy,_], width, height}
  let scale = 1;         // canvas px per map px
  let ready = false;
  let mode = "none";
  let waypoints = {};
  let robot = null;      // {x, y, yaw}
  let drag = null;       // {wx, wy, cx, cy, hx, hy}

  // ---- coordinate transforms (ROS map <-> canvas) ---------------------
  const worldToCanvas = (x, y) => {
    const [ox, oy] = meta.origin;
    const col = (x - ox) / meta.resolution;
    const row = meta.height - (y - oy) / meta.resolution;
    return [col * scale, row * scale];
  };
  const canvasToWorld = (cx, cy) => {
    const [ox, oy] = meta.origin;
    const col = cx / scale, row = cy / scale;
    return [ox + col * meta.resolution,
            oy + (meta.height - row) * meta.resolution];
  };

  // ---- drawing ---------------------------------------------------------
  function draw() {
    if (!ready) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    // waypoints
    ctx.font = "12px system-ui";
    Object.entries(waypoints).forEach(([name, p]) => {
      const [cx, cy] = worldToCanvas(p.x, p.y);
      ctx.fillStyle = "#f5a623";
      ctx.beginPath(); ctx.arc(cx, cy, 5, 0, 7); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.fillText(name, cx + 7, cy + 4);
    });

    // pending click (point + heading arrow)
    if (drag) {
      ctx.strokeStyle = "#38d39f"; ctx.fillStyle = "#38d39f"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(drag.cx, drag.cy, 6, 0, 7); ctx.fill();
      if (drag.hx != null) {
        ctx.beginPath(); ctx.moveTo(drag.cx, drag.cy);
        ctx.lineTo(drag.hx, drag.hy); ctx.stroke();
      }
    }

    // robot pose
    if (robot) {
      const [cx, cy] = worldToCanvas(robot.x, robot.y);
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(-robot.yaw);
      ctx.fillStyle = "#4c8dff";
      ctx.beginPath();
      ctx.moveTo(11, 0); ctx.lineTo(-7, 7); ctx.lineTo(-7, -7); ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
  }

  // ---- load a map for viewing -----------------------------------------
  function loadMap(name) {
    if (!name) return;
    fetch(`/api/map_meta/${name}`).then((r) => r.json()).then((m) => {
      meta = m;
      img.onload = () => {
        const maxW = canvas.parentElement.clientWidth || 640;
        scale = Math.min(1.5, maxW / meta.width);
        canvas.width = Math.round(meta.width * scale);
        canvas.height = Math.round(meta.height * scale);
        ready = true; empty.style.display = "none"; canvas.style.display = "block";
        draw();
      };
      img.src = `/map_image/${name}.png?ts=${Date.now()}`;
    });
    fetch("/api/waypoints").then((r) => r.json()).then((w) => { waypoints = w; draw(); });
  }
  window.mapLoad = loadMap;
  window.mapSetWaypoints = (w) => { waypoints = w; draw(); };
  window.mapSetRobot = (p) => { robot = p; draw(); };

  // ---- mode buttons ----------------------------------------------------
  document.querySelectorAll(".mode").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll(".mode").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); mode = b.dataset.mode;
    };
  });

  // ---- click + drag to place a pose / goal / waypoint -----------------
  function evtCanvas(e) {
    const r = canvas.getBoundingClientRect();
    const p = e.touches ? e.touches[0] : e;
    return { cx: p.clientX - r.left, cy: p.clientY - r.top };
  }
  function down(e) {
    if (!ready || mode === "none") return;
    const { cx, cy } = evtCanvas(e);
    const [wx, wy] = canvasToWorld(cx, cy);
    drag = { wx, wy, cx, cy, hx: null, hy: null };
    draw(); e.preventDefault();
  }
  function moveEvt(e) {
    if (!drag) return;
    const { cx, cy } = evtCanvas(e);
    drag.hx = cx; drag.hy = cy; draw(); e.preventDefault();
  }
  function up() {
    if (!drag) return;
    let yaw = 0;
    if (drag.hx != null) yaw = Math.atan2(-(drag.hy - drag.cy), drag.hx - drag.cx);
    const body = { x: +drag.wx.toFixed(3), y: +drag.wy.toFixed(3), yaw: +yaw.toFixed(3) };
    if (mode === "pose") post("/api/set_pose", body);
    else if (mode === "goal") post("/api/goto", body);
    else if (mode === "save") {
      const name = prompt("waypoint name:");
      if (name) post("/api/waypoints", { name, x: body.x, y: body.y })
        .then((r) => {
          if (!r.ok) { alert(r.error || "save failed"); return; }
          // refresh the whole waypoint UI (list + route dropdown + map overlay)
          if (window.applyWaypoints) window.applyWaypoints(r.waypoints);
          else window.mapSetWaypoints(r.waypoints);
        });
    }
    drag = null; draw();
  }
  // live cursor -> map coordinate readout (top-right)
  const coordEl = document.getElementById("mapCoord");
  canvas.addEventListener("mousemove", (e) => {
    if (!ready) return;
    const { cx, cy } = evtCanvas(e);
    const [wx, wy] = canvasToWorld(cx, cy);
    coordEl.textContent = `x ${wx.toFixed(2)}, y ${wy.toFixed(2)}`;
  });
  canvas.addEventListener("mouseleave", () => { coordEl.textContent = "x —, y —"; });

  canvas.addEventListener("mousedown", down);
  window.addEventListener("mousemove", moveEvt);
  window.addEventListener("mouseup", up);
  canvas.addEventListener("touchstart", down, { passive: false });
  canvas.addEventListener("touchmove", moveEvt, { passive: false });
  canvas.addEventListener("touchend", up);

  // dropdown change -> view that map
  const sel = document.getElementById("mapSelect");
  sel.addEventListener("change", () => loadMap(sel.value));
})();
