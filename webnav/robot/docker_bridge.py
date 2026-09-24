"""UDP bridge between this host process and the ROS2 stack inside the MentorPi
docker container.

  host  --(commands)-->  docker  (UDP :BRIDGE_SEND_PORT)
  host  <--(telemetry)-- docker  (UDP :BRIDGE_RECV_PORT)

Telemetry (pose/imu/battery) is cached so the web UI and sensing loop can read
the latest robot state without touching ROS2 directly.
"""
import json
import socket
import threading
import time

from config import settings


class DockerBridge:
    def __init__(self, docker_ip=None, send_port=None, recv_port=None):
        self.docker_ip = docker_ip or settings.DOCKER_IP
        self.send_port = send_port or settings.BRIDGE_SEND_PORT
        self.recv_port = recv_port or settings.BRIDGE_RECV_PORT

        self._sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock_recv.bind(("0.0.0.0", self.recv_port))

        # latest telemetry from ROS2
        self.state = {"goal": None, "x": 0.0, "y": 0.0, "yaw": 0.0,
                      "linear": 0.0, "angular": 0.0, "battery": None,
                      "updated": 0.0}
        self._lock = threading.Lock()
        self._running = True
        self._t = threading.Thread(target=self._recv_loop, daemon=True)
        self._t.start()

    def _recv_loop(self):
        print(f"[bridge] listening for ROS2 telemetry on UDP {self.recv_port}")
        while self._running:
            try:
                data, _ = self._sock_recv.recvfrom(4096)
                msg = json.loads(data.decode())
                pos = msg.get("pos", {})
                imu = msg.get("imu", {})
                with self._lock:
                    self.state.update({
                        "x": pos.get("x", self.state["x"]) or 0.0,
                        "y": pos.get("y", self.state["y"]) or 0.0,
                        "yaw": pos.get("yaw", self.state["yaw"]) or 0.0,
                        "linear": imu.get("linear_speed", 0.0) or 0.0,
                        "angular": imu.get("angular_speed", 0.0) or 0.0,
                        "battery": msg.get("battery", self.state["battery"]),
                        "goal": msg.get("goal", self.state.get("goal")),
                        "updated": time.time(),
                    })
            except Exception as e:
                print(f"[bridge] recv error: {e}")
                time.sleep(1)

    def get_state(self):
        with self._lock:
            return dict(self.state)

    def send(self, command_dict):
        """Send one command dict to the ROS2 bridge."""
        try:
            command_dict.setdefault("ca_id", settings.ROBOT_ID)
            self._sock_send.sendto(json.dumps(command_dict).encode(),
                                   (self.docker_ip, self.send_port))
            return True
        except Exception as e:
            print(f"[bridge] send error: {e}")
            return False

    def stop(self):
        self._running = False
        try:
            self._sock_recv.close()
            self._sock_send.close()
        except OSError:
            pass
