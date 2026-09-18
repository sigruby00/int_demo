#!/usr/bin/env python3
"""Self-test for the base watchdog in webnav/ros/ros_bridge_docker.py.
Run INSIDE the MentorPi container (ROS env sourced), robot with >= 1 m of free
space in front, nothing else commanding the base:

  python3 watchdog_selftest.py t1   # stale non-zero cmd_vel  -> must stop ~1 s
  python3 watchdog_selftest.py t2   # motors driven behind cmd_vel's back -> stop ~0.3 s
  python3 watchdog_selftest.py t3   # like t2 with odom_publisher frozen (SIGSTOP) -> L2/L3

The robot moves forward at ~0.08 m/s for at most a few seconds in each test.
"""
import math, sys, time, subprocess
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from ros_robot_controller_msgs.msg import MotorsState, MotorState

RPS = 0.08 / (math.pi * 0.065)      # 0.08 m/s forward, mecanum.py convention


class T(Node):
    def __init__(self):
        super().__init__("wd_selftest")
        self.vx = None; self.front = None; self.front_med = None
        self.create_subscription(Odometry, "/odom", lambda m: setattr(self, "vx", m.twist.twist.linear.x), 10)
        self.create_subscription(LaserScan, "/scan_raw", self._scan, 10)
        self.cmd = self.create_publisher(Twist, "/controller/cmd_vel", 10)
        self.mot = self.create_publisher(MotorsState, "/ros_robot_controller/set_motor", 10)

    def _scan(self, m):
        mn = 99.0
        for k, v in enumerate(m.ranges):
            if math.isnan(v) or math.isinf(v): continue
            d = (math.degrees(m.angle_min + k * m.angle_increment) + 180) % 360 - 180
            if abs(d) <= 30: mn = min(mn, v)
        self.front = mn
        med = sorted(v for k, v in enumerate(m.ranges)
                     if not (math.isnan(v) or math.isinf(v))
                     and abs((math.degrees(m.angle_min + k * m.angle_increment) + 180) % 360 - 180) <= 10)
        self.front_med = med[len(med) // 2] if med else None

    def wait(self, s):
        t0 = time.time()
        while time.time() - t0 < s:
            rclpy.spin_once(self, timeout_sec=0.05)

    def motors(self, rps):
        ms = MotorsState()
        for mid, sgn in ((1, 1), (2, 1), (3, -1), (4, -1)):
            m = MotorState(); m.id = mid; m.rps = sgn * rps; ms.data.append(m)
        self.mot.publish(ms)

    def report(self, tag, t0, expect_stopped):
        vx = self.vx if self.vx is not None else float("nan")
        ok = (abs(vx) < 0.02) if expect_stopped else (abs(vx) > 0.03)
        print(f"  t+{time.time()-t0:4.1f}s  {tag:28s} vx={vx:+.3f}  {'OK' if ok else 'FAIL'}")
        return ok


def main():
    test = sys.argv[1] if len(sys.argv) > 1 else "t1"
    rclpy.init(); n = T(); n.wait(2.0)
    if n.front is None or n.front < 1.0:
        print(f"front clearance {n.front} m < 1.0 m, aborting"); return 2
    print(f"front clearance {n.front:.2f} m; test {test}")
    ok = True; t0 = time.time()
    if test == "t1":
        tw = Twist(); tw.linear.x = 0.08; n.cmd.publish(tw)
        print("  published ONE non-zero cmd_vel, then silence")
        n.wait(0.6); ok &= n.report("moving?", t0, False)
        n.wait(1.4); ok &= n.report("stale 1s -> stopped?", t0, True)
    elif test in ("t2", "t3"):
        pid = None
        if test == "t3":
            pid = subprocess.run(["pgrep", "-f", "odom_publisher --ros-args"], capture_output=True, text=True).stdout.split()
            pid = pid[0] if pid else None
            if not pid: print("odom_publisher pid not found"); return 2
            subprocess.run(["kill", "-STOP", pid]); print(f"  odom_publisher pid {pid} FROZEN (SIGSTOP)")
        f0 = n.front_med
        n.motors(RPS); print(f"  drove motors directly via set_motor (bypassing cmd_vel); front median {f0:.3f} m")
        # odom is open loop (mirrors cmd_vel), so judge by the lidar: a runaway at
        # 0.08 m/s shortens the front median by ~8 cm/s; the watchdog needs ~1.5 s
        # (0.5 s persistence + grace + resend) and L2 another 1.5 s.
        try:
            n.wait(4.0)
            f1 = n.front_med; n.wait(2.0); f2 = n.front_med
            moved = f0 - f1; still = f1 - f2
            print(f"  t+4s front median {f1:.3f} (moved {moved*100:+.1f} cm)  t+6s {f2:.3f} (moved {still*100:+.1f} cm more)")
            ok &= moved > 0.04            # it really ran away for a bit
            ok &= abs(still) < 0.03       # ...and the watchdog stopped it
            print("  " + ("OK" if ok else "FAIL"))
        finally:
            if pid:
                subprocess.run(["kill", "-CONT", pid]); print(f"  odom_publisher {pid} resumed (SIGCONT)")
                n.wait(1.0); n.report("after resume", t0, True)
    n.cmd.publish(Twist()); n.wait(0.3)
    print("RESULT", "PASS" if ok else "FAIL"); rclpy.shutdown(); return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
