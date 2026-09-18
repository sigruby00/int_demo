"""ROS2-side bridge, runs INSIDE the MentorPi docker container.

Pairs with robot/docker_bridge.py on the host:
  * PoseSender     : /odom, /imu, battery + TF(map->base_link) -> host UDP :9000
  * CommandReceiver: host UDP :9002 -> teleop cmd_vel / NavigateToPose goals

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
from rclpy.executors import MultiThreadedExecutor

from geometry_msgs.msg import (Twist, PoseStamped, PoseWithCovarianceStamped)
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu
from std_msgs.msg import UInt16
from tf2_ros import Buffer, TransformListener
from nav2_msgs.action import NavigateToPose

HOST_IP = "127.0.0.1"
TELEMETRY_PORT = 9000       # docker -> host
COMMAND_PORT = 9002         # host -> docker
CMD_VEL_TOPIC = "/controller/cmd_vel"


class PoseSender(Node):
    def __init__(self):
        super().__init__("tus_pose_sender")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.imu = {}
        self.battery = None
        self.pos = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        cb = ReentrantCallbackGroup()
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.create_subscription(UInt16, "/ros_robot_controller/battery",
                                 self._battery, 10, callback_group=cb)
        self.create_subscription(Imu, "/imu", self._imu, 10, callback_group=cb)
        self.create_subscription(Odometry, "/odom", self._odom, 10, callback_group=cb)
        self.create_timer(0.5, self._tick, callback_group=cb)

    def _battery(self, msg):
        # controller reports battery as a 0..~105 scaled value -> percent
        self.battery = round(msg.data / 105.0, 1)

    def _imu(self, msg):
        self.imu["angular_velocity_z"] = msg.angular_velocity.z

    def _odom(self, msg):
        self.imu["linear_speed"] = msg.twist.twist.linear.x
        self.imu["angular_speed"] = msg.twist.twist.angular.z

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
        payload = {"pos": self.pos, "imu": self.imu, "battery": self.battery}
        try:
            self.sock.sendto(json.dumps(payload).encode(), (HOST_IP, TELEMETRY_PORT))
        except OSError:
            pass


class CommandReceiver(Node):
    def __init__(self):
        super().__init__("tus_command_receiver")
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
    executor = MultiThreadedExecutor()
    executor.add_node(PoseSender())
    executor.add_node(CommandReceiver())
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
