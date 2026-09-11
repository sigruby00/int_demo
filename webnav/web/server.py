"""int_demo per-Pi web server (webnav).

Runs alongside r_ca_integration.py (the fleet/central-server link). One process
starts the docker bridge, local Wi-Fi sensing, USB joystick reader, camera
preview, and a Flask UI for teleop / SLAM-map selection / waypoints / Nav2
navigation. Adapted from tus_robot_hslab.

  python3 webnav/web/server.py     # from the int_demo repo root
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (Flask, Response, jsonify, render_template, request,
                   send_file, abort)

from config import settings
from robot.docker_bridge import DockerBridge
from robot.sensing import Sensing
from robot.control import Control
from robot.joystick_usb import USBJoystick
from robot.camera import Camera
from robot.recorder import Recorder
from robot import netinfo
from robot import netconfig

app = Flask(__name__, template_folder="templates", static_folder="static")

bridge = DockerBridge()
sensing = Sensing(bridge)
control = Control(bridge)
recorder = Recorder(control)
control.recorder = recorder          # capture all teleop for record/replay
usb_joy = USBJoystick(control)
camera = Camera()


# ---- page ----------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", robot_id=settings.ROBOT_ID)


# ---- live state (polled by the UI) ---------------------------------------
@app.route("/api/state")
def api_state():
    return jsonify({
        "robot_id": settings.ROBOT_ID,
        "sensing": sensing.latest(),
        "state": bridge.get_state(),
        "usb_joystick": usb_joy.status(),
        "roam": sensing.roam_status(),
        "mission": control.mission_status(),
        "current_map": control.current_map(),
        "default_map": control.default_map(),
        "maps": control.list_maps(),
        "camera": camera.available,
        "recorder": recorder.status(),
    })


@app.route("/api/robot_id", methods=["POST"])
def api_robot_id():
    d = request.get_json(force=True, silent=True) or {}
    try:
        rid = int(d.get("id"))
        if not (0 < rid < 1000):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "id must be 1..999"}), 400
    settings.set_robot_id(rid)
    return jsonify({"ok": True, "robot_id": rid})


# ---- network -------------------------------------------------------------
@app.route("/api/net")
def api_net():
    return jsonify({"interfaces": netinfo.all_interfaces(),
                    "eth_config_allowed": netconfig.client_via_eth(request.remote_addr)})


def _require_eth():
    """Guard: Wi-Fi reconfig is only allowed from a client on eth0."""
    if not netconfig.client_via_eth(request.remote_addr):
        return jsonify({"ok": False,
                        "error": "Wi-Fi settings can only be changed over ethernet "
                                 "(connect via eth0 to avoid cutting your own link)."}), 403
    return None


@app.route("/api/net/scan")
def api_net_scan():
    guard = _require_eth()
    if guard:
        return guard
    iface = request.args.get("iface", "wlan1")
    return jsonify({"iface": iface, "networks": netconfig.wifi_scan(iface)})


@app.route("/api/net/ipv4", methods=["POST"])
def api_net_ipv4():
    guard = _require_eth()
    if guard:
        return guard
    d = request.get_json(force=True, silent=True) or {}
    ok, msg = netconfig.set_ipv4(d.get("iface", ""), d.get("method", ""),
                                 d.get("ip"), d.get("gateway"), d.get("dns"))
    return jsonify({"ok": ok, "detail": msg}), (200 if ok else 400)


@app.route("/api/net/connect", methods=["POST"])
def api_net_connect():
    guard = _require_eth()
    if guard:
        return guard
    d = request.get_json(force=True, silent=True) or {}
    ok, msg = netconfig.wifi_connect(d.get("iface", ""), d.get("ssid", ""),
                                     d.get("password"))
    return jsonify({"ok": ok, "detail": msg}), (200 if ok else 400)


# ---- teleop --------------------------------------------------------------
@app.route("/api/drive", methods=["POST"])
def api_drive():
    d = request.get_json(force=True, silent=True) or {}
    control.drive(d.get("linear", 0.0), d.get("angular", 0.0))
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    control.stop()
    return jsonify({"ok": True})


# ---- teleop trajectory record & replay (open-loop, no SLAM) --------------
@app.route("/api/record/start", methods=["POST"])
def api_record_start():
    ok, msg = recorder.start_record()
    return jsonify({"ok": ok, "detail": msg}), (200 if ok else 409)


@app.route("/api/record/stop", methods=["POST"])
def api_record_stop():
    d = request.get_json(force=True, silent=True) or {}
    ok, msg = recorder.stop_record(d.get("name", ""))
    return jsonify({"ok": ok, "detail": msg}), (200 if ok else 400)


@app.route("/api/trajectories", methods=["GET"])
def api_trajectories():
    return jsonify({"trajectories": recorder.list_names()})


@app.route("/api/trajectories/<name>", methods=["DELETE"])
def api_trajectory_del(name):
    return jsonify({"ok": recorder.delete(name), "trajectories": recorder.list_names()})


@app.route("/api/replay/start", methods=["POST"])
def api_replay_start():
    d = request.get_json(force=True, silent=True) or {}
    ok, msg = recorder.start_replay(d.get("name", ""), bool(d.get("loop", True)))
    return jsonify({"ok": ok, "detail": msg}), (200 if ok else 400)


@app.route("/api/replay/stop", methods=["POST"])
def api_replay_stop():
    recorder.stop_replay()
    return jsonify({"ok": True})


# ---- SLAM maps -----------------------------------------------------------
def _map_meta(name):
    """Parse a map's resolution/origin (.yaml) and pixel size (.pgm header)."""
    ypath = os.path.join(settings.MAPS_DIR, name + ".yaml")
    if not os.path.isfile(ypath):
        return None
    res, origin = 0.05, [0.0, 0.0, 0.0]
    with open(ypath) as f:
        for line in f:
            line = line.strip()
            if line.startswith("resolution:"):
                res = float(line.split(":", 1)[1])
            elif line.startswith("origin:"):
                vals = line.split(":", 1)[1].strip().strip("[]")
                origin = [float(v) for v in vals.split(",")]
    # PGM (P5) header: "P5\n<w> <h>\n<maxval>"
    w = h = 0
    pgm = os.path.join(settings.MAPS_DIR, name + ".pgm")
    try:
        with open(pgm, "rb") as f:
            tokens = []
            while len(tokens) < 3:
                tok = b""
                c = f.read(1)
                while c and not c.isspace():
                    tok += c
                    c = f.read(1)
                if tok:
                    tokens.append(tok)
            w, h = int(tokens[1]), int(tokens[2])
    except Exception:
        pass
    return {"name": name, "resolution": res, "origin": origin,
            "width": w, "height": h}


@app.route("/api/maps")
def api_maps():
    return jsonify({"maps": control.list_maps(), "current": control.current_map(),
                    "default": control.default_map()})


@app.route("/api/map_meta/<name>")
def api_map_meta(name):
    meta = _map_meta(name)
    return (jsonify(meta) if meta else (abort(404)))


@app.route("/map_image/<name>.png")
def map_image(name):
    """Render the occupancy .pgm as a PNG for the browser."""
    pgm = os.path.join(settings.MAPS_DIR, name + ".pgm")
    if not os.path.isfile(pgm):
        abort(404)
    try:
        import cv2
        img = cv2.imread(pgm, cv2.IMREAD_GRAYSCALE)
        ok, png = cv2.imencode(".png", img)
        if not ok:
            abort(500)
        return Response(png.tobytes(), mimetype="image/png")
    except Exception:
        # fall back to raw file if opencv is unavailable
        return send_file(pgm, mimetype="image/x-portable-graymap")


@app.route("/api/map", methods=["POST"])
def api_map():
    d = request.get_json(force=True, silent=True) or {}
    ok, msg = control.select_map(d.get("name", ""))
    return jsonify({"ok": ok, "map": msg})


@app.route("/api/set_pose", methods=["POST"])
def api_set_pose():
    d = request.get_json(force=True, silent=True) or {}
    control.set_initial_pose(d.get("x", 0.0), d.get("y", 0.0), d.get("yaw", 0.0))
    return jsonify({"ok": True})


# ---- waypoints -----------------------------------------------------------
@app.route("/api/waypoints", methods=["GET"])
def api_waypoints_get():
    return jsonify(settings.load_waypoints())


@app.route("/api/waypoints", methods=["POST"])
def api_waypoints_add():
    d = request.get_json(force=True, silent=True) or {}
    name = str(d.get("name", "")).strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    wps = settings.load_waypoints()
    # if x/y omitted, capture the robot's current pose
    st = bridge.get_state()
    wps[name] = {"x": float(d.get("x", st["x"])), "y": float(d.get("y", st["y"]))}
    settings.save_waypoints(wps)
    return jsonify({"ok": True, "waypoints": wps})


@app.route("/api/waypoints/<name>", methods=["DELETE"])
def api_waypoints_del(name):
    wps = settings.load_waypoints()
    wps.pop(name, None)
    settings.save_waypoints(wps)
    return jsonify({"ok": True, "waypoints": wps})


# ---- waypoint route / mission -------------------------------------------
@app.route("/api/route", methods=["GET"])
def api_route_get():
    return jsonify({"route": settings.load_route()})


@app.route("/api/route", methods=["POST"])
def api_route_save():
    d = request.get_json(force=True, silent=True) or {}
    route = [str(n) for n in (d.get("route") or [])]
    settings.save_route(route)
    return jsonify({"ok": True, "route": route})


@app.route("/api/mission/start", methods=["POST"])
def api_mission_start():
    d = request.get_json(force=True, silent=True) or {}
    route = d.get("route") or settings.load_route()
    ok, msg = control.start_mission(route, bool(d.get("loop", False)))
    return jsonify({"ok": ok, "route": msg if ok else None, "error": None if ok else msg})


@app.route("/api/mission/stop", methods=["POST"])
def api_mission_stop():
    control.stop_mission()
    return jsonify({"ok": True})


@app.route("/api/goto", methods=["POST"])
def api_goto():
    d = request.get_json(force=True, silent=True) or {}
    if "name" in d:
        ok = control.goto_waypoint(d["name"])
    else:
        ok = control.goto_xy(d.get("x", 0.0), d.get("y", 0.0), d.get("yaw", 0.0))
    return jsonify({"ok": bool(ok)})


# ---- Wi-Fi BS list -------------------------------------------------------
@app.route("/api/bs", methods=["GET"])
def api_bs_get():
    return jsonify(settings.load_bs_list())


@app.route("/api/bs", methods=["POST"])
def api_bs_add():
    d = request.get_json(force=True, silent=True) or {}
    bssid = str(d.get("bssid", "")).strip().lower()
    if not bssid:
        return jsonify({"ok": False, "error": "bssid required"}), 400
    bs = settings.load_bs_list()
    if any(b["bssid"].lower() == bssid for b in bs):
        return jsonify({"ok": False, "error": "already exists"}), 409
    ap_id = int(d.get("ap_id") or (max([b["ap_id"] for b in bs], default=0) + 1))
    bs.append({"ap_id": ap_id, "bssid": bssid})
    settings.save_bs_list(bs)
    return jsonify({"ok": True, "bs": bs})


@app.route("/api/handover", methods=["POST"])
def api_handover():
    d = request.get_json(force=True, silent=True) or {}
    bssid = str(d.get("bssid", "")).strip()
    if not bssid:
        return jsonify({"ok": False, "error": "bssid required"}), 400
    ok, cur = sensing.handover(bssid)
    return jsonify({"ok": bool(ok), "bssid": cur})


@app.route("/api/handover_auto", methods=["POST"])
def api_handover_auto():
    sensing.unlock_roam()
    return jsonify({"ok": True})


@app.route("/api/bs/<bssid>", methods=["DELETE"])
def api_bs_del(bssid):
    bs = [b for b in settings.load_bs_list()
          if b["bssid"].lower() != bssid.lower()]
    settings.save_bs_list(bs)
    return jsonify({"ok": True, "bs": bs})


# ---- camera --------------------------------------------------------------
@app.route("/camera")
def camera_stream():
    return Response(camera.mjpeg_generator(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/camera/snapshot")
def camera_snapshot():
    """Single latest JPEG frame (for the central dashboard's on-demand photo)."""
    jpg = camera.snapshot()
    if not jpg:
        return jsonify({"ok": False, "error": "no frame"}), 503
    return Response(jpg, mimetype="image/jpeg")


def _start_workers():
    sensing.start()
    usb_joy.start()
    camera.start()


def main():
    _start_workers()
    print(f"[web] http://{settings.WEB_HOST}:{settings.WEB_PORT}  (robot {settings.ROBOT_ID})")
    app.run(host=settings.WEB_HOST, port=settings.WEB_PORT,
            threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
