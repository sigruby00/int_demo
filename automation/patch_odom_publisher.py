#!/usr/bin/env python3
"""Patch the MentorPi base driver (controller/odom_publisher_node.py, inside
the MentorPi container) with a cmd_vel timeout + keep-alive:

  * the motor board latches the last speed forever and the stock node forwards
    each cmd_vel exactly once -> one lost packet / dead publisher = runaway.
  * patched: current speeds are re-sent at 10 Hz while commands keep arriving,
    a stop is re-sent 3x, and if no cmd_vel arrives for WD_TIMEOUT s the node
    commands zero on its own (deterministic, no sensors involved).

Patches every copy found (src + install). Idempotent; keeps .orig; --revert.
Takes effect on the next bringup / navigation launch (container restart).

  docker exec -u ubuntu MentorPi python3 /home/ubuntu/shared/int_demo/automation/patch_odom_publisher.py [--revert] [--timeout 0.6]
"""
import argparse, glob, os, shutil, sys

MARK = "# --- int_demo base watchdog ---"
CANDIDATES = (
    "~/ros2_ws/src/driver/controller/controller/odom_publisher_node.py",
    "~/ros2_ws/install/controller/**/odom_publisher_node.py",
)

ap = argparse.ArgumentParser()
ap.add_argument("--revert", action="store_true")
ap.add_argument("--timeout", default="0.6")
a = ap.parse_args()

INIT_HOOK = f'''        self.motor_pub = self.create_publisher(MotorsState, 'ros_robot_controller/set_motor', 1)
        {MARK}
        self._wd_last_cmd = 0.0
        self._wd_last_speeds = None
        self._wd_stopped = True
        self._wd_zero_left = 0
        self.create_timer(0.1, self._wd_tick)
'''

METHODS = f'''
    {MARK}
    # The motor board latches the last speed forever and this node used to
    # forward each cmd_vel exactly once. Now: re-send the current speeds at
    # 10 Hz while commands keep arriving, re-send a stop 3x, and command zero
    # on our own once cmd_vel has been silent for WD_TIMEOUT s.
    WD_TIMEOUT = {a.timeout}

    def _wd_note(self, speeds):
        self._wd_last_cmd = time.time()
        self._wd_last_speeds = speeds
        self._wd_stopped = all(abs(m.rps) < 1e-6 for m in speeds.data)
        self._wd_zero_left = 3 if self._wd_stopped else 0

    def _wd_zero_msg(self):
        zero = MotorsState()
        for m in (self._wd_last_speeds.data if self._wd_last_speeds else []):
            z = type(m)(); z.id = m.id; z.rps = 0.0; zero.data.append(z)
        return zero

    def _wd_tick(self):
        if self._wd_last_speeds is None:
            return
        if self._wd_stopped:
            if self._wd_zero_left > 0:                 # lost-stop insurance
                self._wd_zero_left -= 1
                self.motor_pub.publish(self._wd_zero_msg())
            return
        if time.time() - self._wd_last_cmd > self.WD_TIMEOUT:
            self.linear_x = self.linear_y = self.angular_z = 0.0
            self.motor_pub.publish(self._wd_zero_msg())
            self._wd_stopped = True
            self._wd_zero_left = 2
            self.get_logger().warn('base watchdog: no cmd_vel for %.1fs -> motors stopped' % self.WD_TIMEOUT)
        else:
            self.motor_pub.publish(self._wd_last_speeds)   # keep-alive re-send

'''

def patch(path):
    orig = path + ".orig"
    if a.revert:
        if os.path.exists(orig):
            shutil.copy2(orig, path); print("reverted:", path)
        else:
            print("no backup for", path)
        return 0
    src = open(path).read()
    if MARK in src:
        print("already patched:", path); return 0
    anchor = "        self.motor_pub = self.create_publisher(MotorsState, 'ros_robot_controller/set_motor', 1)\n"
    if anchor not in src or "self.motor_pub.publish(speeds)" not in src or "\ndef main():" not in src:
        print("unexpected content, not patching:", path, file=sys.stderr); return 1
    if not os.path.exists(orig):
        shutil.copy2(path, orig)
    src = src.replace(anchor, INIT_HOOK, 1)
    src = src.replace("self.motor_pub.publish(speeds)",
                      "self.motor_pub.publish(speeds); self._wd_note(speeds)")
    src = src.replace("\ndef main():", METHODS + "\ndef main():", 1)
    open(path, "w").write(src)
    print("patched:", path, "(backup:", orig + ")")
    return 0

paths = []
for c in CANDIDATES:
    paths += [p for p in glob.glob(os.path.expanduser(c), recursive=True) if os.path.isfile(p)]
# a symlinked install copy points at src: patch the real file once
real = {}
for p in paths:
    real.setdefault(os.path.realpath(p), p)
rc = 0
for p in real.values():
    rc |= patch(p)
if not real:
    print("odom_publisher_node.py not found", file=sys.stderr); rc = 1
sys.exit(rc)
