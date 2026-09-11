"""Robot control: teleop driving, waypoint navigation, and SLAM-map selection.

All robot motion goes through the DockerBridge to the ROS2 stack. Map switching
is done by (re)launching the navigation stack inside the MentorPi container with
the chosen map.
"""
import subprocess
import threading
import time

from config import settings

ARRIVE_RADIUS = 0.35        # m: within this of a waypoint counts as "arrived"
WAYPOINT_TIMEOUT = 90.0     # s: give up on a waypoint after this long


class Control:
    def __init__(self, bridge):
        self.bridge = bridge
        self.recorder = None            # set by web server; captures teleop for record/replay
        self._current_map = None
        self._last_drive = 0.0
        # mission (ordered waypoint route) state
        self._mission = {"running": False, "route": [], "index": -1,
                         "target": None, "message": "idle"}
        self._mission_stop = threading.Event()

    # ---- teleop ----------------------------------------------------------
    def drive(self, linear, angular):
        """Direct velocity command (from virtual/USB joystick, keyboard, or replay)."""
        self._last_drive = time.time()
        if self.recorder is not None:
            self.recorder.on_command(linear, angular)
        return self.bridge.send({
            "navigation_mode": "teleop",
            "action": "drive",
            "linear": float(linear),
            "angular": float(angular),
        })

    def stop(self):
        if self.recorder is not None:
            self.recorder.on_command(0.0, 0.0)
        return self.bridge.send({
            "navigation_mode": "teleop", "action": "stop",
            "linear": 0.0, "angular": 0.0,
        })

    # ---- manual localization --------------------------------------------
    def set_initial_pose(self, x, y, yaw=0.0):
        """Manually set the robot's current pose (AMCL /initialpose)."""
        return self.bridge.send({
            "navigation_mode": "set_pose",
            "x": float(x), "y": float(y), "yaw": float(yaw),
        })

    # ---- waypoint navigation --------------------------------------------
    def goto_xy(self, x, y, yaw=0.0):
        """Send a Nav2 goal to an (x, y[, yaw]) map coordinate."""
        return self.bridge.send({
            "navigation_mode": "goal",
            "action": "goto",
            "x": float(x),
            "y": float(y),
            "yaw": float(yaw),
        })

    def goto_waypoint(self, name):
        wps = settings.load_waypoints()
        wp = wps.get(name)
        if not wp:
            return False
        return self.goto_xy(wp["x"], wp["y"])

    # ---- waypoint route mission -----------------------------------------
    def mission_status(self):
        return dict(self._mission)

    def start_mission(self, names):
        """Navigate through the given saved-waypoint names, in order."""
        if self._mission["running"]:
            return False, "mission already running"
        wps = settings.load_waypoints()
        route = [n for n in names if n in wps]
        if not route:
            return False, "no valid waypoints in route"
        self._mission_stop.clear()
        self._mission.update({"running": True, "route": route, "index": -1,
                              "target": None, "message": "starting"})
        threading.Thread(target=self._mission_loop, args=(route,), daemon=True).start()
        return True, route

    def stop_mission(self):
        self._mission_stop.set()
        self.stop()
        self._mission.update({"running": False, "message": "stopped"})
        return True

    def _mission_loop(self, route):
        wps = settings.load_waypoints()
        for i, name in enumerate(route):
            if self._mission_stop.is_set():
                break
            wp = wps.get(name)
            if not wp:
                continue
            self._mission.update({"index": i, "target": name,
                                  "message": f"going to {name} ({i+1}/{len(route)})"})
            self.goto_xy(wp["x"], wp["y"])
            if not self._wait_arrival(wp["x"], wp["y"]):
                if self._mission_stop.is_set():
                    break
                self._mission.update({"message": f"timeout at {name}, continuing"})
                continue
            self._mission.update({"message": f"reached {name}"})
            time.sleep(1.0)     # brief dwell at each waypoint
        done = not self._mission_stop.is_set()
        self._mission.update({"running": False, "target": None,
                              "message": "route complete" if done else "stopped"})

    def _wait_arrival(self, gx, gy):
        t0 = time.time()
        while time.time() - t0 < WAYPOINT_TIMEOUT:
            if self._mission_stop.is_set():
                return False
            st = self.bridge.get_state()
            if ((st["x"] - gx) ** 2 + (st["y"] - gy) ** 2) ** 0.5 <= ARRIVE_RADIUS:
                return True
            time.sleep(0.4)
        return False

    # ---- SLAM map selection ---------------------------------------------
    def list_maps(self):
        """Available maps (by .yaml stem) in the maps dir."""
        import glob, os
        return sorted(os.path.splitext(os.path.basename(p))[0]
                      for p in glob.glob(os.path.join(settings.MAPS_DIR, "*.yaml")))

    def select_map(self, map_name):
        """Launch the navigation stack for `map_name` as the SOLE ROS graph.

        The MentorPi boot bringup and navigation.launch.py each start the lidar
        and base nodes. Running them together duplicates nodes, starves the lidar
        serial port and exhausts DDS shared-memory ports, which prevents the nav2
        costmaps from activating -> no obstacles -> the robot drives through
        walls. To guarantee a single clean graph we restart the container (which
        clears every prior node) and then launch navigation once as sole owner.
        """
        if map_name not in self.list_maps():
            return False, f"unknown map: {map_name}"

        def _worker():
            # 1) clean slate: restart the container (kills all prior ROS nodes)
            subprocess.run(["docker", "restart", settings.DOCKER_NAME],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=60)
            time.sleep(12)                       # let the container come back up
            # 2) launch navigation as the only ROS app (base + nav2, one lidar)
            launch = (f"source {settings.ROS_WS}/.zshrc >/dev/null 2>&1; "
                      f"export need_compile=${{need_compile:-False}}; "
                      f"nohup ros2 launch navigation navigation.launch.py "
                      f"map:={map_name} > /tmp/nav_{map_name}.log 2>&1 &")
            subprocess.Popen(["docker", "exec", "-u", "ubuntu", settings.DOCKER_NAME,
                              "/bin/zsh", "-c", launch],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self._current_map = map_name
        threading.Thread(target=_worker, daemon=True).start()
        return True, map_name

    def current_map(self):
        return self._current_map

    def default_map(self):
        """Preferred map preselected in the web nav dropdown. Uses
        settings.DEFAULT_MAP when it exists, else the first available map."""
        maps = self.list_maps()
        if settings.DEFAULT_MAP in maps:
            return settings.DEFAULT_MAP
        return maps[0] if maps else None
