#!/bin/bash
# Remote "update code + restart the whole stack", triggered from the central
# dashboard (socket.io "update_restart"). r_ca_integration launches this
# DETACHED (setsid) so it survives the very sessions it is about to kill.
#
#   1) pull latest main   2) stop all three tmux sessions   3) relaunch
#
# No sudo: auto_start.sh recreates the sessions (and pulls again), so this works
# on every robot whether or not the boot service is installed.
set -u
REPO="/home/pi/docker/tmp/int_demo"
cd "$REPO" || exit 1

echo "[update_restart] $(date) pulling latest..."
git fetch --all && git reset --hard origin/main

echo "[update_restart] stopping stack..."
tmux kill-session -t int_demo   2>/dev/null
tmux kill-session -t robot_web  2>/dev/null
tmux kill-session -t ros_bridge 2>/dev/null
sleep 2

echo "[update_restart] relaunching..."
exec /bin/bash "$REPO/automation/auto_start.sh"
