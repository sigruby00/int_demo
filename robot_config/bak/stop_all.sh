#!/bin/bash
# stop_ros_monitor.sh

echo "[INFO] Stopping Int-demo related Python processes..."

# int_demo 관련 python 프로세스만 골라서 종료
PIDS=$(ps aux | grep '[i]nt_demo' | grep 'python' | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "[INFO] No int demo processes found."
else
    echo "[INFO] Killing PIDs: $PIDS"
    kill -9 $PIDS
    echo "[INFO] All int demo processes terminated."
fi