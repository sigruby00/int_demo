#!/bin/bash
cd /home/pi/docker/tmp/int_demo

# 1. 최신 코드 pull (필요시 주석 해제)
echo "[INFO] Pulling latest code from GitHub..."
git pull origin main
sleep 5

# Execute the Gstreamer script
echo "[INFO] Starting ros_monitor_docker.py..."
docker exec -u ubuntu -w /home/ubuntu MentorPi /bin/zsh -c "source ~/.zshrc; python3 /home/ubuntu/shared/int_demo/gstreamer_usb_cam.py > /tmp/ros_monitor.log 2>&1 &"
sleep 2

# Execute the Iperf clinet

echo "[INFO] Starting iperf client..."
# ./execute_iperf_client.sh
docker exec -u ubuntu -w /home/ubuntu MentorPi /bin/bash -c "/home/ubuntu/shared/int_demo/iperf3_client.sh"