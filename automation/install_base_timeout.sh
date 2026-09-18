#!/bin/bash
# Run ON THE ROBOT HOST. Installs the cmd_vel timeout / keep-alive into the
# MentorPi base driver (see automation/patch_odom_publisher.py). Idempotent.
# Takes effect on the next bringup/nav launch.
#   ./install_base_timeout.sh            # install (timeout 0.6 s)
#   ./install_base_timeout.sh 0.8        # custom timeout
#   ./install_base_timeout.sh --revert
DOCKER_NAME=${DOCKER_NAME:-MentorPi}
D=docker; command -v docker >/dev/null 2>&1 || D="sudo docker"
if [ "$1" = "--revert" ]; then
  $D exec -u ubuntu "$DOCKER_NAME" python3 /home/ubuntu/shared/int_demo/automation/patch_odom_publisher.py --revert
else
  $D exec -u ubuntu "$DOCKER_NAME" python3 /home/ubuntu/shared/int_demo/automation/patch_odom_publisher.py --timeout "${1:-0.6}"
fi
