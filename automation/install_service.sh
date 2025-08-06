#!/bin/bash

SERVICE_NAME="int_demo"
SCRIPT_PATH="/home/pi/docker/tmp/int_demo/automation/auto_start.sh"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "[INFO] Creating systemd service for SV Robot..."

# 1. 서비스 파일 생성
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Start Int Demo Automation Script
After=network-online.target NetworkManager.service docker.service
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/docker/tmp/int_demo
Environment=HOME=/home/pi SHELL=/bin/zsh PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
ExecStart=/bin/bash /home/pi/docker/tmp/sv_robot/automation/auto_start.sh
StandardOutput=journal
StandardError=journal
Restart=no

[Install]
WantedBy=multi-user.target
EOF

# 2. 실행 권한 부여
chmod +x "$SCRIPT_PATH"

# 3. systemd 등록 및 활성화
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl start "$SERVICE_NAME"

echo "[INFO] Service ${SERVICE_NAME} installed and started."