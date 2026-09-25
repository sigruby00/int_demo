#!/bin/bash
# int_demo boot startup (fleet integration + per-Pi web/nav).
#
# Starts, each in its own resilient tmux session:
#   int_demo   : r_ca_integration.py  (central-server link, streaming, sensing, handover)
#   ros_bridge : webnav/ros/ros_bridge_docker.py  (ROS2<->host UDP, inside MentorPi container)
#   robot_web  : webnav/web/server.py  (per-Pi web UI: teleop / nav / maps / waypoints)
#
# The MentorPi container is NOT restarted here: teleop + telemetry work on top of
# the stock boot bringup. Navigation (nav2 costmaps) is enabled ON DEMAND when a
# map is selected from the web UI (which then does a clean container restart).
set -u

REPO_HOST="/home/pi/docker/tmp/int_demo"          # host path (git repo)
REPO_DOCKER="/home/ubuntu/shared/int_demo"        # same repo seen inside the container
DOCKER_NAME="MentorPi"
ROS_WS="/home/ubuntu/ros2_ws"

cd "$REPO_HOST" || exit 1

# --- keep robots on latest main (fleet auto-update) ----------------------
# ---- clock: get the one-time chrony step (to the TO's clock) done BEFORE ROS is
# used. A step while nav2 runs breaks AMCL/costmap (scans older than the TF cache).
echo "[INFO] Waiting for chrony to sync to the TO (up to 3 min)..."
BASE0=$(python3 -c "import time; print(time.time()-time.monotonic())")
chronyc waitsync 36 1 0 5 >/dev/null 2>&1 && echo "[INFO] chrony synced" || echo "[WARN] chrony not synced yet (TO unreachable?) - continuing"
sudo -n chronyc makestep >/dev/null 2>&1; sleep 1
BASE1=$(python3 -c "import time; print(time.time()-time.monotonic())")
STEP=$(python3 -c "print(round($BASE1-$BASE0, 2))")
echo "[INFO] clock step applied at boot: ${STEP}s (chrony offset now $(chronyc tracking 2>/dev/null | awk -F': ' '/Last offset/ {print $2}'))"
if python3 -c "import sys; sys.exit(0 if abs($STEP) > 1.0 else 1)"; then
  echo "[INFO] clock stepped by ${STEP}s -> restarting MentorPi so bringup/lidar use the corrected clock"
  docker restart MentorPi >/dev/null 2>&1 || sudo -n docker restart MentorPi >/dev/null 2>&1
  sleep 20
fi
if [ -f /boot/firmware/config.txt ] && ! grep -q "^usb_max_current_enable=1" /boot/firmware/config.txt; then
  echo "[INFO] enabling usb_max_current_enable=1 (Pi 5 USB budget 0.6 A -> 1.6 A; takes effect after the next reboot)"
  echo "usb_max_current_enable=1" | sudo -n tee -a /boot/firmware/config.txt >/dev/null
fi
echo "[INFO] Pulling latest code from origin/main..."
git fetch --all && git reset --hard origin/main
sleep 3

# --- 1) fleet integration (central server, streaming, sensing, handover) -
if ! tmux has-session -t int_demo 2>/dev/null; then
  echo "[INFO] tmux 'int_demo': r_ca_integration.py"
  tmux new-session -d -s int_demo -n shell
  tmux send-keys -t int_demo:1 \
    "cd $REPO_HOST && while true; do python3 ./r_ca_integration.py; echo '[WARN] r_ca_integration ended, restart in 3s'; sleep 3; done" C-m
fi

# --- 1b) lidar near-range filter (idempotent, patches the LD19 launch) ------
# Guarantees every robot drops its own body/antenna/cable returns (< 0.30 m)
# on every boot, even after a container/workspace refresh. Takes effect on the
# next bringup/nav launch (auto-load nav below restarts the container anyway).
echo "[INFO] Ensuring lidar scan filter is installed..."
bash "$REPO_HOST/automation/install_scan_filter.sh" 2>&1 | sed 's/^/[scan_filter] /' || true
# --- 1c) base driver cmd_vel timeout / keep-alive (idempotent) --------------
# The motor board latches the last speed; without this one lost stop packet or
# a dead publisher = runaway. See automation/patch_odom_publisher.py.
echo "[INFO] Ensuring nav2 speed limits (+15 %) are installed..."
bash "$REPO_HOST/automation/install_nav_speed.sh" 1.15 2>&1 | sed 's/^/[nav_speed] /' || true
echo "[INFO] Ensuring base cmd_vel timeout is installed..."
bash "$REPO_HOST/automation/install_base_timeout.sh" 2>&1 | sed 's/^/[base_timeout] /' || true

# --- 2) sync SLAM maps into the ROS2 workspace (for on-demand nav) --------
docker exec -u ubuntu "$DOCKER_NAME" /bin/bash -lc \
  "mkdir -p $ROS_WS/src/slam/maps && cp -rf $REPO_DOCKER/webnav/config/maps/* $ROS_WS/src/slam/maps/ 2>/dev/null || true" \
  >/dev/null 2>&1 || true

# --- 3) ROS2<->host bridge inside the container (resilient) ---------------
if ! tmux has-session -t ros_bridge 2>/dev/null; then
  echo "[INFO] tmux 'ros_bridge': webnav/ros/ros_bridge_docker.py"
  tmux new-session -d -s ros_bridge -n shell
  mkdir -p /home/pi/int_demo_logs && tmux pipe-pane -t ros_bridge:1 -o "cat >> /home/pi/int_demo_logs/ros_bridge.log"
  tmux send-keys -t ros_bridge:1 \
    "while true; do docker exec -u ubuntu -w $REPO_DOCKER $DOCKER_NAME /bin/zsh -c 'source $ROS_WS/.zshrc; python3 webnav/ros/ros_bridge_docker.py'; echo '[WARN] ros_bridge ended, retry in 3s'; sleep 3; done" C-m
fi

# --- 4) per-Pi web server (resilient) ------------------------------------
if ! tmux has-session -t clock_guard 2>/dev/null; then
  echo "[INFO] tmux 'clock_guard': automation/clock_guard.py"
  tmux new-session -d -s clock_guard -n shell
  mkdir -p /home/pi/int_demo_logs && tmux pipe-pane -t clock_guard:1 -o "cat >> /home/pi/int_demo_logs/clock_guard.log"
  tmux send-keys -t clock_guard:1 \
    "cd $REPO_HOST && while true; do python3 automation/clock_guard.py; echo '[WARN] clock_guard ended, restart in 3s'; sleep 3; done" C-m
fi
if ! tmux has-session -t board_guard 2>/dev/null; then
  echo "[INFO] tmux 'board_guard': automation/board_guard.py"
  tmux new-session -d -s board_guard -n shell
  mkdir -p /home/pi/int_demo_logs && tmux pipe-pane -t board_guard:1 -o "cat >> /home/pi/int_demo_logs/board_guard.log"
  tmux send-keys -t board_guard:1 \
    "cd $REPO_HOST && while true; do python3 automation/board_guard.py; echo '[WARN] board_guard ended, restart in 3s'; sleep 3; done" C-m
fi
if ! tmux has-session -t robot_web 2>/dev/null; then
  echo "[INFO] tmux 'robot_web': webnav/web/server.py"
  tmux new-session -d -s robot_web -n shell
  mkdir -p /home/pi/int_demo_logs && tmux pipe-pane -t robot_web:1 -o "cat >> /home/pi/int_demo_logs/robot_web.log"
  tmux send-keys -t robot_web:1 \
    "cd $REPO_HOST && while true; do python3 webnav/web/server.py; echo '[WARN] web crashed, restart in 3s'; sleep 3; done" C-m
fi

# --- 5) auto-load navigation with the default map --------------------------
# Bring up nav2+AMCL automatically (once webnav is serving) so remote
# set_pose / goal / mission work without a manual "Load nav". select_map does
# a clean container restart + launches navigation as the sole ROS graph.
setsid bash -c '
  for i in $(seq 1 40); do
    dm=$(curl -s http://127.0.0.1:8081/api/maps 2>/dev/null \
         | python3 -c "import sys,json;print(json.load(sys.stdin).get(\"default\") or \"\")" 2>/dev/null)
    if [ -n "$dm" ]; then
      curl -s -X POST -H "Content-Type: application/json" \
           -d "{\"name\":\"$dm\"}" http://127.0.0.1:8081/api/map >/dev/null 2>&1
      break
    fi
    sleep 2
  done
' >/tmp/auto_load_nav.log 2>&1 < /dev/null &

echo "[INFO] Done. Web UI on http://<robot-ip>:8081"
