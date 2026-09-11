# int_demo

Per-CA robot stack for the NICT integrated demonstration. Each MentorPi robot runs
**two cooperating parts**:

1. **Fleet integration** — `r_ca_integration.py`
   Central-server link (Socket.IO), camera **video streaming** (GStreamer → TO),
   UDP traffic generation, Wi-Fi **sensing → server**, and **server-commanded
   Wi-Fi handover**. This is the original demo behaviour.

2. **Per-Pi web / navigation** — `webnav/` *(added, adapted from `tus_robot_hslab`)*
   A local Flask web UI on **`http://<robot-ip>:8081`** for **teleop**, **SLAM
   map selection**, **waypoints / route missions (Nav2)**, USB joystick, camera
   preview, and Wi-Fi BS-list management — for easy external monitoring/control
   of each robot. It drives the MentorPi ROS2 stack through a host↔docker UDP
   bridge and does **not** replace the fleet link; both run together.

## Layout
```
r_ca_integration.py        fleet integration (server link, streaming, sensing, handover)
config.py                  per-robot id (ca_id / to_id) — gitignored, one per robot
automation/auto_start.sh   boot: tmux sessions  int_demo + ros_bridge + robot_web
webnav/
  config/settings.py       webnav config (robot id inherits ca_id; ports, paths)
  config/maps/             SLAM maps (.pgm/.yaml)
  robot/                   host-side: docker_bridge, control (teleop/nav/mission/map),
                           sensing, camera, joystick_usb, netinfo, netconfig
  ros/ros_bridge_docker.py ROS2-side bridge (runs INSIDE the MentorPi container):
                           /odom /imu /battery/TF → host:9000 ; host:9002 → cmd_vel / NavigateToPose
  web/server.py + ui       Flask app + REST API + templates/static
```

## Architecture (webnav)
```
 Browser ──HTTP──▶ webnav/web/server.py (Flask, host)
                     ├─ robot/sensing.py       wpa_cli scan → RSSI
                     ├─ robot/control.py       teleop / goto / mission / map select
                     ├─ robot/camera.py        OpenCV → MJPEG preview
                     └─ robot/docker_bridge.py ─UDP:9002─▶ ROS2 (docker)
                                               ◀─UDP:9000─ telemetry
 webnav/ros/ros_bridge_docker.py  (inside the MentorPi container)
```
Teleop + telemetry work on top of the stock boot bringup. **Navigation (nav2
costmaps) is enabled on demand**: selecting a map from the web UI cleanly restarts
the container and launches `navigation.launch.py` as the sole ROS graph.

## Run
```bash
# boot brings all three up via automation/auto_start.sh (systemd: automation/install_service.sh)
# manual:
python3 ./r_ca_integration.py                     # fleet link + streaming
python3 webnav/web/server.py                       # per-Pi web UI  (http://<ip>:8081)
# inside the MentorPi container (ROS env sourced):
python3 webnav/ros/ros_bridge_docker.py
```

## Notes
- `webnav/config/{robot_id,bs_list,waypoints,route}.json` are **per-robot runtime
  state** (gitignored), edited from the web UI — pulls never clobber them.
- The web camera preview uses `/dev/video0` by default to avoid conflicting with
  the GStreamer stream on `/dev/video2`.
- Host-side deps (on the MentorPi Pi image): flask, opencv-python, pygame, psutil.
