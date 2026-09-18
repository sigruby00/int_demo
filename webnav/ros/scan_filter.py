"""Near-range LaserScan filter, runs INSIDE the MentorPi docker container.

The LD19 lidar sits in the middle of the chassis and sees the robot's own
parts (body edges, the Wi-Fi antenna, dangling cables, ...) as returns at
2-30 cm. The ldlidar driver hard-codes range_min=0.02 and has no range
filter, so slam_toolbox / nav2 rasterize those returns as obstacles right at
the robot's own position -> smeared "dirty" maps and phantom obstacles.

Wiring (see automation/install_scan_filter.sh):
  ldlidar driver -> /scan_ld19 -> [this node] -> /scan_raw -> slam / nav / apps

Returns closer than MIN_RANGE are replaced with NaN, which karto/slam_toolbox
and nav2 both treat as "no return" (neither obstacle nor free space).

  python3 scan_filter.py [--in /scan_ld19] [--out /scan_raw] [--min-range 0.30]
"""
import argparse
import math

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import LaserScan


class ScanFilter(Node):
    def __init__(self, topic_in, topic_out, min_range):
        super().__init__("scan_near_filter")
        self.min_range = float(min_range)
        self.dropped = 0
        self.total = 0
        # driver publishes reliable; keep the same so every existing
        # subscriber (slam_toolbox, nav2, lidar app) still matches.
        qos = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST, depth=10)
        self.pub = self.create_publisher(LaserScan, topic_out, qos)
        self.create_subscription(LaserScan, topic_in, self._cb, qos)
        self.create_timer(30.0, self._report)
        self.get_logger().info(
            f"{topic_in} -> {topic_out}, dropping returns < {self.min_range:.2f} m")

    def _cb(self, msg):
        nan = float("nan")
        mr = self.min_range
        r = list(msg.ranges)
        n = 0
        for i, v in enumerate(r):
            if v == v and v < mr:          # v == v is False for NaN
                r[i] = nan
                n += 1
        msg.ranges = r
        msg.range_min = max(msg.range_min, mr)
        self.dropped += n
        self.total += len(r)
        self.pub.publish(msg)

    def _report(self):
        if self.total:
            self.get_logger().info(
                f"dropped {self.dropped}/{self.total} pts "
                f"({100.0 * self.dropped / self.total:.1f}%) in last 30 s")
        self.dropped = self.total = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="topic_in", default="/scan_ld19")
    ap.add_argument("--out", dest="topic_out", default="/scan_raw")
    ap.add_argument("--min-range", type=float, default=0.30)
    args = ap.parse_args()
    rclpy.init()
    node = ScanFilter(args.topic_in, args.topic_out, args.min_range)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
