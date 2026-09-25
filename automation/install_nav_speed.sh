#!/bin/bash
# Scale nav2 speed limits (velocity_smoother max_velocity, DWB max_vel_x/theta/xy,
# spin max_rotational_vel) in the container's navigation config. Idempotent.
# Usage: install_nav_speed.sh [factor]   |   install_nav_speed.sh --revert
DOCKER_NAME=${DOCKER_NAME:-MentorPi}
D=docker; command -v docker >/dev/null 2>&1 || D="sudo docker"
if [ "$1" = "--revert" ]; then
  $D exec -u ubuntu "$DOCKER_NAME" python3 /home/ubuntu/shared/int_demo/automation/patch_nav_speed.py --revert
else
  $D exec -u ubuntu "$DOCKER_NAME" python3 /home/ubuntu/shared/int_demo/automation/patch_nav_speed.py --factor "${1:-1.15}"
fi
