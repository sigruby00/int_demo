"""Host-side base fail-safe (deadman) for the MentorPi motor board.

The ROS graph (bringup + nav2 + our bridge) runs inside the MentorPi container.
If that whole graph freezes, every software timeout in it freezes too and the
motor board keeps the last wheel command for ever -- the "drives forward for
minutes" runaway. The host web process is a separate process outside ROS: it
receives the bridge's 2 Hz telemetry, and when that telemetry stops for
STALE_S it writes the board-level "all motors 0" frame straight to the motor
board's USB serial port (repeatedly, until telemetry comes back). Stopping an
idle robot is harmless, so the guard fires on any telemetry loss.
"""
import os
import struct
import threading
import time

DEVICES = ("/dev/rrc", "/dev/ttyACM0")   # motor board serial (udev symlink, raw)
STALE_S = 2.5                              # telemetry silence that arms the stop
PERIOD_S = 0.5                             # stop frame cadence while armed


def _crc8(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c >> 1) ^ 0x8C) if (c & 1) else (c >> 1)
    return c & 0xFF


def stop_packet():
    """set_motor_speed([[1,0],[2,0],[3,0],[4,0]]) as the SDK writes it."""
    data = [0x01, 4]
    for mid in range(4):
        data.extend(struct.pack("<Bf", mid, 0.0))
    body = [3, len(data)] + data                  # PACKET_FUNC_MOTOR = 3
    return bytes([0xAA, 0x55] + body + [_crc8(bytes(body))])


def write_stop():
    for dev in DEVICES:
        if not os.path.exists(dev):
            continue
        try:
            try:
                import serial
                with serial.Serial(dev, 1000000, timeout=0.2, write_timeout=0.2) as sp:
                    sp.write(stop_packet())
            except ImportError:
                fd = os.open(dev, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
                try:
                    os.write(fd, stop_packet())
                finally:
                    os.close(fd)
            return dev
        except Exception as e:
            print(f"[failsafe] write to {dev} failed: {e}")
    return None


class Deadman:
    def __init__(self, last_telemetry_fn):
        self._last = last_telemetry_fn          # -> wall time of the last telemetry
        self.armed = False
        self.events = 0
        self.stops = 0
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(PERIOD_S)
            try:
                last = self._last()
                if not last:                      # never heard from the bridge yet
                    continue
                age = time.time() - last
                if age > STALE_S:
                    if not self.armed:
                        self.armed = True
                        self.events += 1
                        print(f"[failsafe] bridge telemetry silent {age:.1f}s -> holding motors at 0 (serial)", flush=True)
                    if write_stop():
                        self.stops += 1
                elif self.armed:
                    self.armed = False
                    print(f"[failsafe] telemetry back after {self.stops} stop frames", flush=True)
                    self.stops = 0
            except Exception as e:
                print(f"[failsafe] {e}", flush=True)

    def status(self):
        return {"armed": self.armed, "events": self.events}
