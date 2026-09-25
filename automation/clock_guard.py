#!/usr/bin/env python3
"""Clock-step guard (host side, outside ROS).

A system-clock jump (chrony stepping to the TO's clock) breaks ROS: the lidar
driver keeps stamping scans with the old time base, AMCL/costmap drop every
scan ("earlier than all the data in the transform cache"), and nav2 drives on
odometry alone. Detect a jump (wall clock vs monotonic clock diverge by more
than STEP_S between two polls), stop any running route, and restart the
MentorPi container so bringup + lidar come back on the corrected clock. The
web UI mission message tells the operator to re-select the map and Set pose.
"""
import json, subprocess, time, urllib.request

STEP_S = 1.0
POLL_S = 3.0
WEB = "http://127.0.0.1:8081"


def log(*a):
    print(time.strftime("%H:%M:%S"), "[clock_guard]", *a, flush=True)


def post(path, body=None):
    try:
        req = urllib.request.Request(WEB + path, data=json.dumps(body or {}).encode(),
                                     headers={"content-type": "application/json"})
        return urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        return f"error: {e}"


def main():
    ref = time.time() - time.monotonic()
    log(f"armed (step threshold {STEP_S}s, poll {POLL_S}s)")
    while True:
        time.sleep(POLL_S)
        now = time.time() - time.monotonic()
        jump = now - ref
        ref = now
        if abs(jump) < STEP_S:
            continue
        log(f"SYSTEM CLOCK STEPPED by {jump:+.1f}s -> stopping route, restarting MentorPi container")
        log("mission stop:", post("/api/mission/stop"))
        try:
            subprocess.run(["docker", "restart", "MentorPi"], timeout=120, check=False)
            log("container restarted; operator must re-select the map and Set pose")
        except Exception as e:
            log(f"container restart failed: {e}")
        ref = time.time() - time.monotonic()       # re-base after the restart


if __name__ == "__main__":
    main()
