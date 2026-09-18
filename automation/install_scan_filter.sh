#!/bin/bash
# Run ON THE ROBOT HOST. Installs the near-range scan filter into the MentorPi
# container's LD19 lidar launch (see automation/patch_ld19_launch.py).
# Takes effect on the next bringup/slam launch.
#   ./install_scan_filter.sh            # install (min range 0.30 m)
#   ./install_scan_filter.sh 0.25       # custom min range
#   ./install_scan_filter.sh --revert   # restore original launch
DOCKER_NAME=${DOCKER_NAME:-MentorPi}
D=docker; command -v docker >/dev/null 2>&1 || D="sudo docker"
if [ "$1" = "--revert" ]; then
  $D exec -u ubuntu "$DOCKER_NAME" python3 /home/ubuntu/shared/int_demo/automation/patch_ld19_launch.py --revert
else
  $D exec -u ubuntu "$DOCKER_NAME" python3 /home/ubuntu/shared/int_demo/automation/patch_ld19_launch.py --min-range "${1:-0.30}"
fi
