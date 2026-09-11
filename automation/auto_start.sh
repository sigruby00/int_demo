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

# --- 2) sync SLAM maps into the ROS2 workspace (for on-demand nav) --------
docker exec -u ubuntu "$DOCKER_NAME" /bin/bash -lc \
  "mkdir -p $ROS_WS/src/slam/maps && cp -rf $REPO_DOCKER/webnav/config/maps/* $ROS_WS/src/slam/maps/ 2>/dev/null || true" \
  >/dev/null 2>&1 || true

# --- 3) ROS2<->host bridge inside the container (resilient) ---------------
if ! tmux has-session -t ros_bridge 2>/dev/null; then
  echo "[INFO] tmux 'ros_bridge': webnav/ros/ros_bridge_docker.py"
  tmux new-session -d -s ros_bridge -n shell
  tmux send-keys -t ros_bridge:1 \
    "while true; do docker exec -u ubuntu -w $REPO_DOCKER $DOCKER_NAME /bin/zsh -c 'source $ROS_WS/.zshrc; python3 webnav/ros/ros_bridge_docker.py'; echo '[WARN] ros_bridge ended, retry in 3s'; sleep 3; done" C-m
fi

# --- 4) per-Pi web server (resilient) ------------------------------------
if ! tmux has-session -t robot_web 2>/dev/null; then
  echo "[INFO] tmux 'robot_web': webnav/web/server.py"
  tmux new-session -d -s robot_web -n shell
  tmux send-keys -t robot_web:1 \
    "cd $REPO_HOST && while true; do python3 webnav/web/server.py; echo '[WARN] web crashed, restart in 3s'; sleep 3; done" C-m
fi

echo "[INFO] Done. Web UI on http://<robot-ip>:8081"
