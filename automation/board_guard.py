#!/usr/bin/env python3
"""Motor-board link guard (host side, outside ROS).

The MentorPi motor board keeps executing its last wheel command until a new
frame arrives. Its USB serial link (CDC ACM, /dev/rrc -> ttyACMn) drops and
re-enumerates at random (seen as ttyACM0 -> ttyACM1 while driving): the
ros_robot_controller node then holds a dead handle, every ROS-side stop goes
nowhere, and the robot drives straight until someone cuts the power. This has
shown up in line following, teleop and navigation alike.

Detection (every 0.5 s):
  * /dev/rrc now points at a different ttyACM node than before, or
  * the bridge telemetry is fresh but the battery message age (published by
    the controller node from the board's own reports) exceeds LINK_DEAD_S.
Reaction:
  1. stop frames to /dev/rrc (the symlink follows the re-enumerated node) at
     10 Hz for RECOVER_S, mission stop through the web API,
  2. restart the container (so its /dev shows the new node) and the bringup
     service (start_node) so the controller re-opens the board,
  3. keep sending stop frames until the battery reports flow again.
"""
import json, os, subprocess, sys, time, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webnav"))
from robot import base_failsafe as fs

LINK_DEAD_S = 3.0
RECOVER_S = 4.0
WEB = "http://127.0.0.1:8081"


def log(*a):
    print(time.strftime("%H:%M:%S"), "[board_guard]", *a, flush=True)


def state():
    try:
        d = json.load(urllib.request.urlopen(WEB + "/api/state", timeout=3))
        return d.get("state") or {}
    except Exception:
        return {}


def post(path):
    try:
        req = urllib.request.Request(WEB + path, data=b"{}", headers={"content-type": "application/json"})
        return urllib.request.urlopen(req, timeout=4).read()[:60]
    except Exception as e:
        return f"error: {e}"


def rrc_target():
    try:
        return os.path.realpath("/dev/rrc")
    except Exception:
        return None


def stop_burst(seconds):
    t0 = time.time(); n = 0
    while time.time() - t0 < seconds:
        if fs.write_stop():
            n += 1
        time.sleep(0.1)
    return n


def main():
    last = rrc_target(); armed_at = time.time(); events = 0
    log(f"armed: /dev/rrc -> {last}, link-dead threshold {LINK_DEAD_S}s")
    while True:
        time.sleep(0.5)
        cur = rrc_target()
        st = state()
        tel_age = time.time() - (st.get("updated") or 0)
        bat_age = st.get("battery_age")
        reenum = cur != last and cur is not None
        link_dead = (tel_age < 2.0 and bat_age is not None and bat_age > LINK_DEAD_S)
        if not (reenum or link_dead):
            continue
        events += 1
        why = f"/dev/rrc moved {last} -> {cur}" if reenum else f"battery reports stale {bat_age}s (controller lost the board)"
        log(f"MOTOR BOARD LINK LOST ({why}) -> stop frames + mission stop")
        last = cur
        log("mission stop:", post("/api/mission/stop"))
        n = stop_burst(RECOVER_S); log(f"{n} stop frames sent to {cur}")
        log("restarting MentorPi container + bringup (start_node)...")
        subprocess.run(["docker", "restart", "MentorPi"], timeout=120, check=False)
        stop_burst(2.0)
        subprocess.run(["sudo", "-n", "systemctl", "restart", "start_node"], timeout=60, check=False)
        # keep the base stopped until the controller talks to the board again
        t0 = time.time()
        while time.time() - t0 < 90:
            stop_burst(1.0)
            st = state(); ba = st.get("battery_age")
            if ba is not None and ba < 2.0 and time.time() - (st.get("updated") or 0) < 2.0:
                break
        log(f"recovered: battery reports flowing again ({time.time()-t0:.0f}s); operator must re-select the map and Set pose")
        last = rrc_target()


if __name__ == "__main__":
    main()
