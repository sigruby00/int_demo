#!/bin/bash
cd /home/pi/docker/tmp/int_demo

# 1. 최신 코드 pull (필요시 주석 해제)
echo "[INFO] Pulling latest code from GitHub..."
git fetch --all
git reset --hard origin/main
sleep 5

# Execute the Gstreamer script
echo "[INFO] Starting GStreamer Sender ..."
python3 ./r_ca_integration.py