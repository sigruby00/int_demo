"""Teleop trajectory record & replay (open-loop, velocity-based).

No SLAM / no localization: we record the velocity commands (linear, angular)
issued while the robot is driven manually (web joystick / keyboard / USB pad),
timestamped, and replay that velocity profile back through the same
DockerBridge -> cmd_vel path, looping. The robot repeats the same motion.

Replay resamples the profile at a fixed rate and keeps publishing the active
velocity, so the base controller's cmd_vel timeout never stops it mid-segment.
Open-loop means small drift accumulates over many laps (expected).
"""
import json
import os
import threading
import time

from config import settings

TRAJ_FILE = os.path.join(settings.BASE_DIR, "config", "trajectories.json")
REPLAY_HZ = 10.0                      # resample / publish rate during replay


class Recorder:
    def __init__(self, control):
        self.control = control        # used to send drive/stop during replay
        self.recording = False
        self._t0 = 0.0
        self._buf = []                # [[t, linear, angular], ...]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._replay = {"running": False, "name": None, "loop": False,
                        "lap": 0, "elapsed": 0.0, "duration": 0.0}

    # ---- persistence -----------------------------------------------------
    def _load(self):
        try:
            with open(TRAJ_FILE) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, d):
        with open(TRAJ_FILE, "w") as f:
            json.dump(d, f, indent=2)

    def list_names(self):
        return sorted(self._load().keys())

    def delete(self, name):
        d = self._load()
        existed = d.pop(name, None) is not None
        self._save(d)
        return existed

    # ---- record ----------------------------------------------------------
    def start_record(self):
        if self._replay["running"]:
            return False, "cannot record during replay"
        with self._lock:
            self._buf = [[0.0, 0.0, 0.0]]     # start at rest
            self._t0 = time.time()
            self.recording = True
        return True, "recording"

    def on_command(self, linear, angular):
        """Called by Control on every drive/stop while recording."""
        if not self.recording:
            return
        with self._lock:
            self._buf.append([round(time.time() - self._t0, 3),
                              float(linear), float(angular)])

    def stop_record(self, name):
        with self._lock:
            if not self.recording:
                return False, "not recording"
            self.recording = False
            self._buf.append([round(time.time() - self._t0, 3), 0.0, 0.0])  # final stop
            buf = list(self._buf)
        name = (name or "").strip()
        if not name:
            return False, "name required"
        if buf[-1][0] < 0.3:
            return False, "recording too short"
        d = self._load()
        d[name] = {"segments": buf, "duration": buf[-1][0]}
        self._save(d)
        return True, {"name": name, "points": len(buf), "duration": buf[-1][0]}

    # ---- replay ----------------------------------------------------------
    def start_replay(self, name, loop=True):
        if self.recording:
            return False, "cannot replay while recording"
        if self._replay["running"]:
            return False, "already replaying"
        traj = self._load().get(name)
        if not traj or not traj.get("segments"):
            return False, f"unknown/empty trajectory: {name}"
        self._stop.clear()
        self._replay.update({"running": True, "name": name, "loop": bool(loop),
                             "lap": 0, "elapsed": 0.0,
                             "duration": traj["segments"][-1][0]})
        threading.Thread(target=self._replay_loop, args=(traj["segments"],),
                         daemon=True).start()
        return True, {"name": name, "segments": len(traj["segments"]), "loop": bool(loop)}

    def stop_replay(self):
        self._stop.set()
        self.control.stop()
        self._replay.update({"running": False, "elapsed": 0.0})
        return True

    @staticmethod
    def _vel_at(segs, t):
        vel = (0.0, 0.0)
        for st, lin, ang in segs:
            if st <= t:
                vel = (lin, ang)
            else:
                break
        return vel

    def _replay_loop(self, segs):
        T = max(segs[-1][0], 0.001)
        tick = 1.0 / REPLAY_HZ
        try:
            lap = 0
            while not self._stop.is_set():
                lap += 1
                self._replay["lap"] = lap
                t_ref = time.time()
                while not self._stop.is_set():
                    el = time.time() - t_ref
                    if el > T:
                        break
                    self._replay["elapsed"] = round(el, 2)
                    lin, ang = self._vel_at(segs, el)
                    self.control.drive(lin, ang)
                    time.sleep(tick)
                if not self._replay["loop"]:
                    break
        finally:
            self.control.stop()
            self._replay.update({"running": False, "elapsed": 0.0})

    # ---- status ----------------------------------------------------------
    def status(self):
        with self._lock:
            pts = len(self._buf)
            rec_dur = round(time.time() - self._t0, 1) if self.recording else 0.0
        return {"recording": self.recording, "record_points": pts,
                "record_elapsed": rec_dur, "replay": dict(self._replay),
                "trajectories": self.list_names()}
