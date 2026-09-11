"""Configuration for the int_demo per-Pi web/nav stack (`webnav`).

Adapted from tus_robot_hslab. This runs ALONGSIDE the fleet integration
(`r_ca_integration.py`, which keeps the central-server link + streaming +
handover): webnav adds a per-robot web UI for teleop / SLAM-map selection /
waypoints / Nav2 navigation, driving the MentorPi ROS2 stack via a host<->docker
UDP bridge. The robot id is inherited from int_demo's per-robot `config.py`
(`ca_id`) unless overridden in `robot_id.json`.
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- robot identity -------------------------------------------------------
# Persisted in config/robot_id.json; editable from the web UI. Default 1.
ROBOT_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "robot_id.json")


def _ca_id_from_int_demo():
    """int_demo runs the fleet integration with a per-robot `config.py` (gitignored)
    at the repo root that defines `ca_id`. Reuse it so the web robot id matches the
    CA id automatically. Returns int or None."""
    root_cfg = os.path.join(os.path.dirname(BASE_DIR), "config.py")  # int_demo/config.py
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_int_demo_root_cfg", root_cfg)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return int(getattr(mod, "ca_id"))
    except Exception:
        return None


def load_robot_id():
    # 1) explicit web-edited id wins
    try:
        with open(ROBOT_ID_FILE) as f:
            return int(json.load(f).get("id"))
    except (OSError, ValueError, TypeError):
        pass
    # 2) else inherit int_demo's CA id
    cid = _ca_id_from_int_demo()
    if cid is not None:
        return cid
    # 3) fallback
    return 1


ROBOT_ID = load_robot_id()


def set_robot_id(n):
    """Update the live robot id and persist it."""
    global ROBOT_ID
    ROBOT_ID = int(n)
    with open(ROBOT_ID_FILE, "w") as f:
        json.dump({"id": ROBOT_ID}, f)
    return ROBOT_ID

# ---- network interfaces (Wi-Fi sensing) ----------------------------------
# wlan1 is the interface whose association actually moves / is measured.
WLAN_SENSE_IFACE = "wlan1"
MOVING_AVG_N = 5                      # RSSI smoothing window (scan samples)
SENSE_PERIOD_S = 0.5                  # sensing/print cadence
SCAN_PERIOD_S = 10.0                  # active wpa_cli scan cadence

# ---- host <-> ROS2(docker) bridge (UDP) ----------------------------------
# Host sends commands to the docker ROS bridge on SEND_PORT; docker publishes
# pose/imu telemetry back to the host on RECV_PORT.
DOCKER_IP = "127.0.0.1"
BRIDGE_SEND_PORT = 9002              # host -> docker (commands)
BRIDGE_RECV_PORT = 9000             # docker -> host (telemetry)

# ---- MentorPi docker / ROS2 ----------------------------------------------
DOCKER_NAME = "MentorPi"
ROS_WS = "/home/ubuntu/ros2_ws"
# Maps live in the docker workspace (synced from ./config/maps on boot).
MAPS_DIR = os.path.join(BASE_DIR, "config", "maps")

# ---- camera (local preview only, no server streaming) --------------------
CAMERA_DEVICE = "/dev/video0"
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 15

# ---- web server -----------------------------------------------------------
WEB_HOST = "0.0.0.0"
WEB_PORT = 8081                      # 8080 is taken by the Hiwonder web_video_serve

# ---- editable state files (managed from the web UI) ----------------------
BS_LIST_FILE = os.path.join(BASE_DIR, "config", "bs_list.json")
WAYPOINTS_FILE = os.path.join(BASE_DIR, "config", "waypoints.json")
ROUTE_FILE = os.path.join(BASE_DIR, "config", "route.json")


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# Built-in default BS list, used when config/bs_list.json is absent (e.g. a fresh
# clone). The live file is gitignored runtime state edited from the web UI, so
# updates never clobber it.
DEFAULT_BS_LIST = [
    {"ap_id": 1, "bssid": "14:84:73:74:8e:2f"},
    {"ap_id": 2, "bssid": "14:84:73:74:7b:4f"},
    {"ap_id": 3, "bssid": "14:84:73:74:95:8f"},
    {"ap_id": 4, "bssid": "14:84:73:74:8e:8f"},
]


def load_bs_list():
    """Return the Wi-Fi BS list: [{"ap_id": int, "bssid": "aa:bb:.."}, ...]."""
    return _load_json(BS_LIST_FILE, list(DEFAULT_BS_LIST))


def save_bs_list(bs_list):
    with open(BS_LIST_FILE, "w") as f:
        json.dump(bs_list, f, indent=2)


def load_waypoints():
    """Return named waypoints: {"name": {"x": float, "y": float}, ...}."""
    return _load_json(WAYPOINTS_FILE, {})


def save_waypoints(wps):
    with open(WAYPOINTS_FILE, "w") as f:
        json.dump(wps, f, indent=2)


def load_route():
    """Return the ordered route: ["wpName", ...]."""
    return _load_json(ROUTE_FILE, [])


def save_route(route):
    with open(ROUTE_FILE, "w") as f:
        json.dump(route, f, indent=2)
