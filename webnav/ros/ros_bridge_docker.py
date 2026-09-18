"""ROS2-side bridge, runs INSIDE the MentorPi docker container.

Pairs with robot/docker_bridge.py on the host:
  * PoseSender     : /odom, battery + TF(map->base_link) -> host UDP :9000
  * CommandReceiver: host UDP :9002 -> teleop cmd_vel / NavigateToPose goals
  * BaseWatchdog   : (inside CommandReceiver) stops the base when the last
                     velocity command is stale or the wheels keep turning after
                     a zero command (lost motor packet) -> no more runaways

Deliberately dependency-light: only rclpy + standard MentorPi message types that
already exist in the container. No connection to any external server.

  ros2 run ... is not needed; launched via:
  python3 ros_bridge_docker.py
"""
import json
import math
import socket
import threading

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import SingleThreadedExecutor

from geometry_msgs.msg import (Twist, PoseStamped, PoseWithCovarianceStamped)
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu, LaserScan
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import UInt16
from tf2_ros import Buffer, TransformListener
from nav2_msgs.action import NavigateToPose
from ros_robot_controller_msgs.msg import MotorsState, MotorState

HOST_IP = "127.0.0.1"
TELEMETRY_PORT = 9000       # docker -> host
COMMAND_PORT = 9002         # host -> docker
CMD_VEL_TOPIC = "/controller/cmd_vel"
MOTOR_TOPIC = "/ros_robot_controller/set_motor"
RRC_DEVICE = "/dev/rrc"     # motor board serial (USB CDC), same as the SDK


def _crc8(data):
    # CRC-8/MAXIM, identical to ros_robot_controller_sdk.checksum_crc8
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c >> 1) ^ 0x8C) if (c & 1) else (c >> 1)
    return c & 0xFF


def rrc_stop_packet():
    """Board-level 'all 4 motors 0 rps' frame, byte-for-byte what the SDK's
    set_motor_speed([[1,0],[2,0],[3,0],[4,0]]) writes."""
    import struct
    data = [0x01, 4]
    for mid in range(4):
        data.extend(struct.pack("<Bf", mid, 0.0))
    body = [3, len(data)] + data                  # PACKET_FUNC_MOTOR = 3
    return bytes([0xAA, 0x55] + body + [_crc8(bytes(body))])


class Sampler(Node):
    """High-rate topics (/odom ~100 Hz, battery) are NOT spun by the main
    executor: they use KEEP_LAST(1) and are polled ~10x/s via spin_once, so the
    middleware discards the intermediate samples in C++ instead of a Python
    callback running for every single message (this bridge was burning ~40%
    of a Pi core on 3x100 Hz callbacks, starving nav2/AMCL)."""

    def __init__(self):
        super().__init__("tus_sampler")
        self.odom = None          # latest nav_msgs/Odometry
        self.battery = None
        self.gyro_z = 0.0
        self.scan = None          # latest LaserScan
        self._scan_hist = []      # [(t, ranges)] ~4 Hz, last ~1.5 s
        q1 = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                        history=HistoryPolicy.KEEP_LAST, depth=1)
        self.create_subscription(Odometry, "/odom", self._odom, q1)
        self.create_subscription(UInt16, "/ros_robot_controller/battery",
                                 self._battery, q1)
        self.create_subscription(Imu, "/imu", self._imu, q1)
        self.create_subscription(LaserScan, "/scan_raw", self._scan, q1)
        self._exec = SingleThreadedExecutor()
        self._exec.add_node(self)

    def _odom(self, msg):
        self.odom = msg

    def _battery(self, msg):
        # controller reports battery as a 0..~105 scaled value -> percent
        self.battery = round(msg.data / 105.0, 1)

    def _imu(self, msg):
        self.gyro_z = msg.angular_velocity.z

    def _scan(self, msg):
        self.scan = msg

    def poll(self):
        # drain whatever is pending (at most one per subscription with depth 1)
        for _ in range(6):
            self._exec.spin_once(timeout_sec=0.0)

    # -- lidar motion detector ------------------------------------------------
    # The MentorPi base has NO wheel feedback (odom_raw just integrates the
    # commanded velocity), so the only way to know the wheels really stopped is
    # to look at the world: median |delta range| between the current scan and
    # the one ~1 s earlier. Stationary: ~0.5-1 cm (noise). Driving 0.1 m/s or
    # spinning: several cm. Robust to a person walking past (median).
    LIDAR_WINDOW = 1.0            # s
    LIDAR_MOVING_M = 0.03         # m median change over the window

    def lidar_motion(self, now):
        sc = self.scan
        if sc is None:
            return None
        if not self._scan_hist or now - self._scan_hist[-1][0] >= 0.25:
            self._scan_hist.append((now, sc.ranges))
            self._scan_hist = [h for h in self._scan_hist if now - h[0] <= 1.6]
        old = None
        for t, r in self._scan_hist:
            if now - t >= self.LIDAR_WINDOW:
                old = r
        if old is None or len(old) != len(sc.ranges):
            return None
        d = []
        for a, b in zip(old, sc.ranges):
            if a == a and b == b and a != float("inf") and b != float("inf"):
                d.append(abs(a - b))
        if len(d) < 50:
            return None
        d.sort()
        return d[len(d) // 2]

    def speeds(self):
        if self.odom is None:
            return 0.0, 0.0, 0.0
        t = self.odom.twist.twist
        return t.linear.x, t.linear.y, t.angular.z


class PoseSender(Node):
    def __init__(self, sampler):
        super().__init__("tus_pose_sender")
        self.sampler = sampler
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.pos = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.create_timer(0.5, self._tick)

    def _tick(self):
        try:
            if self.tf_buffer.can_transform("map", "base_link", rclpy.time.Time()):
                tr = self.tf_buffer.lookup_transform("map", "base_link", rclpy.time.Time())
                t, q = tr.transform.translation, tr.transform.rotation
                self.pos = {
                    "x": t.x, "y": t.y,
                    "yaw": math.atan2(2 * (q.w * q.z + q.x * q.y),
                                      1 - 2 * (q.y * q.y + q.z * q.z)),
                }
        except Exception:
            pass
        vx, _, wz = self.sampler.speeds()
        imu = {"linear_speed": vx, "angular_speed": wz, "angular_velocity_z": wz}
        payload = {"pos": self.pos, "imu": imu, "battery": self.sampler.battery}
        try:
            self.sock.sendto(json.dumps(payload).encode(), (HOST_IP, TELEMETRY_PORT))
        except OSError:
            pass


class CommandReceiver(Node):
    def __init__(self, sampler):
        super().__init__("tus_command_receiver")
        self.sampler = sampler
        self.cmd_vel = self.create_publisher(Twist, CMD_VEL_TOPIC, 30)
        self.initpose_pub = self.create_publisher(
            PoseWithCovarianceStamped, "/initialpose", 10)
        # Created lazily on the first nav goal: the NavigateToPose action server
        # only exists once navigation (nav2) is launched. Building the ActionClient
        # up-front can fail ("context invalid") on a stock-bringup graph or when
        # Fast-DDS shared memory is stale, which would kill the whole bridge and
        # take teleop + telemetry down with it. Lazy creation keeps the bridge up.
        self.nav_client = None
        # ---- teleop watchdog ------------------------------------------------
        # The base controller latches the last cmd_vel and has no timeout: if a
        # teleop "stop" is ever lost (missed keyup / closed page / dropped UDP),
        # the robot keeps executing the last command forever (spins/drives until
        # a bringup restart). We therefore treat teleop as a HEARTBEAT: keep
        # republishing the last teleop velocity only while fresh commands keep
        # arriving, and publish a final zero once they stop. When idle we publish
        # nothing, so nav2 keeps full control of cmd_vel.
        import time as _t
        self._t = _t
        self._tele_cmd = Twist()
        self._tele_until = 0.0        # republish _tele_cmd while now <= this
        self._tele_idle = True
        self.create_timer(1.0 / 15.0, self._tele_tick)
        self.TELE_HOLD = 0.6          # s: no heartbeat within this -> auto-stop
        # ---- base watchdog (all modes: teleop / nav2 / apps) -----------------
        # cmd_vel -> odom_publisher -> set_motor -> serial -> motor board is
        # purely event driven with no timeout anywhere and the board latches the
        # last speed forever. One lost/garbled packet (or a publisher that dies
        # or stalls mid-motion) = the robot drives off in a straight line until
        # bringup is restarted. We watch BOTH velocity topics and the wheel
        # odometry and force a stop when
        #   A) the last command was non-zero but nothing arrived for WD_STALE s
        #   B) the last command was zero (or none) but the wheels still turn
        #      after WD_ZERO_GRACE s  -> the stop packet was lost: resend it
        self.WD_STALE = 1.0
        self.WD_ZERO_GRACE = 0.3
        self.WD_MOVING_LIN = 0.02     # m/s   odom thresholds (odom is OPEN LOOP:
        self.WD_MOVING_ANG = 0.05     # rad/s  it mirrors odom_publisher's last cmd)
        self.WD_GYRO = 0.15           # rad/s  real rotation while commanded zero
        self.WD_ZERO_RESEND = (0.15, 0.4, 0.8)   # s after a zero: resend it (lost-packet insurance)
        self._wd_zero_at = 0.0
        self._wd_zero_sent = 0
        self._wd_world_since = 0.0
        self._wd_last_cmd_nonzero = False
        self._wd_last_cmd_time = 0.0
        self._wd_stale_fired = False
        self._wd_events = 0
        self._wd_last_log = 0.0
        self._wd_since = 0.0          # when the current runaway was first seen
        # escalation when cmd_vel zeros do not stop the wheels: the ROS chain
        # below us (odom_publisher / ros_robot_controller executor / serial)
        # is wedged, which is exactly the state only a bringup restart used to fix
        self.WD_L2_AFTER = 1.5        # s: publish set_motor zeros directly
        self.WD_L3_AFTER = 3.0        # s: write the stop frame to the serial port
        self.motor_pub = self.create_publisher(MotorsState, MOTOR_TOPIC, 10)
        qwd = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST, depth=10)
        self.create_subscription(Twist, CMD_VEL_TOPIC, self._wd_cmd, qwd)   # teleop/apps (+ our own)
        self.create_subscription(Twist, "/cmd_vel", self._wd_cmd, qwd)      # nav2 velocity_smoother
        self.create_timer(0.1, self._wd_tick)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", COMMAND_PORT))
        threading.Thread(target=self._recv_loop, daemon=True).start()
        self.get_logger().info(f"listening for commands on UDP {COMMAND_PORT}")

    def _tele_tick(self):
        now = self._t.time()
        if now <= self._tele_until:
            self.cmd_vel.publish(self._tele_cmd)      # heartbeat active
        elif not self._tele_idle:
            self.cmd_vel.publish(Twist())             # heartbeat stopped -> final zero
            self._tele_idle = True                    # then go idle (yield to nav)

    # ---- base watchdog --------------------------------------------------------
    def _wd_cmd(self, msg):
        nz = (abs(msg.linear.x) > 1e-6 or abs(msg.linear.y) > 1e-6
              or abs(msg.angular.z) > 1e-6)
        now = self._t.time()
        if not nz and self._wd_last_cmd_nonzero:
            # a stop after motion: schedule redundant zeros (see WD_ZERO_RESEND)
            self._wd_zero_at = now
            self._wd_zero_sent = 0
        self._wd_last_cmd_nonzero = nz
        self._wd_last_cmd_time = now
        self._wd_stale_fired = False

    def _wd_stop(self, why, moving):
        now = self._t.time()
        self.cmd_vel.publish(Twist())                              # L1
        self._wd_events += 1
        if not moving:
            self._wd_since = 0.0
            level = "L1"
        else:
            if self._wd_since == 0.0:
                self._wd_since = now
            held = now - self._wd_since
            level = "L1"
            if held > self.WD_L2_AFTER:                            # L2
                level = "L2 set_motor"
                ms = MotorsState()
                for mid in (1, 2, 3, 4):
                    m = MotorState(); m.id = mid; m.rps = 0.0
                    ms.data.append(m)
                self.motor_pub.publish(ms)
            if held > self.WD_L3_AFTER:                            # L3
                level = "L3 serial"
                try:
                    try:
                        import serial
                        with serial.Serial(RRC_DEVICE, 1000000, timeout=0.2,
                                           write_timeout=0.2) as sp:
                            sp.write(rrc_stop_packet())
                    except ImportError:
                        import os
                        fd = os.open(RRC_DEVICE, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
                        try:
                            os.write(fd, rrc_stop_packet())
                        finally:
                            os.close(fd)
                except Exception as e:
                    self.get_logger().error(f"base watchdog L3 serial write failed: {e}")
        if now - self._wd_last_log > 1.0:
            self._wd_last_log = now
            vx, vy, wz = self.sampler.speeds()
            self.get_logger().warn(
                f"base watchdog STOP #{self._wd_events} [{level}]: {why} "
                f"(wheel odom vx={vx:.2f} vy={vy:.2f} wz={wz:.2f})")

    def _wd_tick(self):
        self.sampler.poll()
        now = self._t.time()
        if now <= self._tele_until:
            return                                    # teleop heartbeat owns the base
        age = now - self._wd_last_cmd_time
        # redundant zeros after a stop (only while nobody asked to move again)
        if (not self._wd_last_cmd_nonzero and self._wd_zero_at
                and self._wd_zero_sent < len(self.WD_ZERO_RESEND)
                and now - self._wd_zero_at >= self.WD_ZERO_RESEND[self._wd_zero_sent]):
            self._wd_zero_sent += 1
            self.cmd_vel.publish(Twist())
        vx, vy, wz = self.sampler.speeds()
        odom_moving = (abs(vx) > self.WD_MOVING_LIN or abs(vy) > self.WD_MOVING_LIN
                       or abs(wz) > self.WD_MOVING_ANG)
        # world-based motion: gyro (rotation) or lidar scene change (any motion),
        # must persist >= 0.5 s to count
        lm = self.sampler.lidar_motion(now)
        world = (abs(self.sampler.gyro_z) > self.WD_GYRO
                 or (lm is not None and lm > self.sampler.LIDAR_MOVING_M))
        if world:
            if self._wd_world_since == 0.0:
                self._wd_world_since = now
        else:
            self._wd_world_since = 0.0
        world_moving = world and now - self._wd_world_since >= 0.5
        moving = odom_moving or world_moving
        if self._wd_last_cmd_nonzero and age > self.WD_STALE and not self._wd_stale_fired:
            self._wd_stale_fired = True               # -> our own zero re-arms via _wd_cmd
            self._wd_stop(f"no velocity command for {age:.1f}s while commanded to move", moving)
        elif not self._wd_last_cmd_nonzero and moving and age > self.WD_ZERO_GRACE:
            why = ("robot still moving after a zero/no command "
                   f"(odom={odom_moving} gyro={self.sampler.gyro_z:.2f} lidar_d={lm if lm is None else round(lm, 3)})")
            self._wd_stop(why, moving)
        elif not moving:
            self._wd_since = 0.0

    def _recv_loop(self):
        while rclpy.ok():
            try:
                data, _ = self.sock.recvfrom(2048)
                cmd = json.loads(data.decode())
                self._handle(cmd)
            except Exception as e:
                self.get_logger().error(f"cmd error: {e}")

    def _handle(self, cmd):
        mode = cmd.get("navigation_mode")
        action = cmd.get("action")
        if mode == "teleop":
            tw = Twist()
            if action == "drive":
                tw.linear.x = float(cmd.get("linear", 0.0))
                tw.angular.z = float(cmd.get("angular", 0.0))
                # arm the heartbeat: keep publishing this until commands stop
                self._tele_cmd = tw
                self._tele_until = self._t.time() + self.TELE_HOLD
                self._tele_idle = False
                self.cmd_vel.publish(tw)
            else:  # "stop" -> immediate zero and disarm heartbeat
                self._tele_cmd = Twist()
                self._tele_until = 0.0
                self._tele_idle = True
                self.cmd_vel.publish(Twist())
        elif mode == "goal" and action == "goto":
            self._send_goal(float(cmd.get("x", 0.0)), float(cmd.get("y", 0.0)),
                            float(cmd.get("yaw", 0.0)))
        elif mode == "set_pose":
            self._set_initial_pose(float(cmd.get("x", 0.0)),
                                   float(cmd.get("y", 0.0)),
                                   float(cmd.get("yaw", 0.0)))

    def _set_initial_pose(self, x, y, yaw):
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = x
        msg.pose.pose.position.y = y
        msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(yaw / 2.0)
        # modest covariance so AMCL accepts and refines it
        cov = [0.0] * 36
        cov[0] = cov[7] = 0.25          # x, y
        cov[35] = 0.068                 # yaw
        msg.pose.covariance = cov
        self.initpose_pub.publish(msg)
        self.get_logger().info(f"set initial pose ({x:.2f}, {y:.2f}, {yaw:.2f})")

    def _send_goal(self, x, y, yaw=0.0):
        if self.nav_client is None:
            try:
                self.nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")
            except Exception as e:
                self.get_logger().warn(f"could not create nav action client: {e}")
                return
        if not self.nav_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().warn("navigate_to_pose action server not available")
            return
        goal = NavigateToPose.Goal()
        p = PoseStamped()
        p.header.frame_id = "map"
        p.header.stamp = self.get_clock().now().to_msg()
        p.pose.position.x = x
        p.pose.position.y = y
        p.pose.orientation.z = math.sin(yaw / 2.0)
        p.pose.orientation.w = math.cos(yaw / 2.0)
        goal.pose = p
        self.nav_client.send_goal_async(goal)
        self.get_logger().info(f"goto ({x:.2f}, {y:.2f})")


def main():
    rclpy.init()
    sampler = Sampler()
    executor = SingleThreadedExecutor()
    executor.add_node(PoseSender(sampler))
    executor.add_node(CommandReceiver(sampler))
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
